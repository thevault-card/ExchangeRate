"""소스 -> 판정 -> 적재 배선. 로그는 stdout JSON 한 줄. (설계 §9-2)"""
import json
from datetime import datetime
from decimal import Decimal
from enum import Enum

from . import alerts, db, sources
from .config import FX_TABLE, INDEX_TABLE, KST, PROVISIONAL

INDEX_OUTLIER_THRESHOLD = Decimal("0.10")  # 지수는 환율보다 변동성이 크다 (설계 §9-1)
FX_OUTLIER_THRESHOLD = Decimal("0.05")


class JobStatus(str, Enum):
    SUCCESS = "success"
    MARKET_CLOSED = "market_closed"  # 알림 대상 아님. 종료코드 0
    FAILURE = "failure"  # 종료코드 1


def _log(**fields) -> None:
    print(json.dumps(fields, ensure_ascii=False, default=str), flush=True)


def _batch_id(job: str, now: datetime) -> str:
    return f"{job}-{now.isoformat()}"


def run_index(index_code: str, *, now: datetime, lookback_days: int = 5) -> int:
    job = f"index_{index_code.lower()}"
    batch_id = _batch_id(job, now)

    points = sources.fetch_index(index_code, lookback_days)

    with db.connect() as conn:
        # 비어 있어도 접속한다 — 그래야 이번 실행이 0건이어도 staleness/freshness
        # 판정이 살아있다 (예: 토요일 06:30 실행이 미국 금요일분을 이미 받은 뒤
        # 그 다음 실행에서 0건이 나오는 경우도 최신 적재일 기준으로 계속 검사된다).
        previous = _latest_close(conn, index_code) if points else None
        written = 0
        if points:
            written = db.upsert_index(
                conn, points, provisional=PROVISIONAL[index_code], batch_id=batch_id
            )
            conn.commit()
        latest = db.latest_date(conn, INDEX_TABLE, "index_code", index_code, "trade_date")
        alerts.check_freshness(index_code, latest, now)

    status = JobStatus.SUCCESS if points else JobStatus.MARKET_CLOSED
    log_fields = {
        "job": job, "batch_id": batch_id, "status": status.value,
        "fetched": len(points), "written": written,
    }
    if points:
        newest = max(points, key=lambda p: p[1])
        warning = alerts.check_outlier(previous, newest[2], threshold=INDEX_OUTLIER_THRESHOLD)
        log_fields.update(
            latest_date=newest[1], latest_close=newest[2],
            provisional=PROVISIONAL[index_code], warning=warning,
        )
    _log(**log_fields)
    return 0


def run_fx(*, now: datetime) -> int:
    job = "fx_daily"
    batch_id = _batch_id(job, now)
    today = now.date()

    point = sources.fetch_fx(today)

    with db.connect() as conn:
        previous = _latest_rate(conn) if point else None
        written = 0
        if point:
            written = db.upsert_fx(conn, point, batch_id=batch_id)
            conn.commit()
        latest = db.latest_date(conn, FX_TABLE, "currency_code", "USD", "rate_date")
        alerts.check_freshness("FX", latest, now)

    status = JobStatus.SUCCESS if point else JobStatus.MARKET_CLOSED
    log_fields = {
        "job": job, "batch_id": batch_id, "status": status.value,
        "fetched": 1 if point else 0, "written": written,
    }
    if point:
        warning = alerts.check_outlier(previous, point[2], threshold=FX_OUTLIER_THRESHOLD)
        log_fields.update(rate_date=point[1], base_rate=point[2], warning=warning)
    _log(**log_fields)
    return 0


def _latest_close(conn, index_code: str) -> Decimal | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT close_value FROM {INDEX_TABLE} WHERE index_code = %s "
            f"ORDER BY trade_date DESC LIMIT 1",
            (index_code,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _latest_rate(conn) -> Decimal | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT base_rate FROM {FX_TABLE} WHERE currency_code = 'USD' "
            f"ORDER BY rate_date DESC LIMIT 1"
        )
        row = cur.fetchone()
    return row[0] if row else None


JOBS = {
    "index_spx": lambda now, days: run_index("SPX", now=now, lookback_days=days),
    "index_kospi": lambda now, days: run_index("KOSPI", now=now, lookback_days=days),
    "fx_daily": lambda now, days: run_fx(now=now),  # 환율은 당일 1건만. days 를 안 쓴다
}

DEFAULT_LOOKBACK_DAYS = 5  # 평소 수집. 배치가 하루 실패해도 다음 날 메워진다


def run(job_name: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> int:
    if job_name not in JOBS:
        _log(error=f"알 수 없는 배치: {job_name}", available=sorted(JOBS))
        return 2
    now = datetime.now(KST)
    try:
        return JOBS[job_name](now, lookback_days)
    except (alerts.BatchFailure, sources.SourceError) as exc:
        _log(job=job_name, status=JobStatus.FAILURE.value, error=str(exc))
        return 1
