"""소스 -> 판정 -> 적재 배선. 로그는 stdout JSON 한 줄. (설계 §9-2)"""
import json
from datetime import datetime
from decimal import Decimal

from . import alerts, db, sources
from .config import FX_TABLE, INDEX_TABLE, KST, PROVISIONAL

INDEX_OUTLIER_THRESHOLD = Decimal("0.10")  # 지수는 환율보다 변동성이 크다 (설계 §9-1)
FX_OUTLIER_THRESHOLD = Decimal("0.05")


def _log(**fields) -> None:
    print(json.dumps(fields, ensure_ascii=False, default=str), flush=True)


def _batch_id(job: str, now: datetime) -> str:
    return f"{job}-{now.isoformat()}"


def run_index(index_code: str, *, now: datetime, lookback_days: int = 5) -> int:
    job = f"index_{index_code.lower()}"
    batch_id = _batch_id(job, now)
    today = now.date()

    points = sources.fetch_index(index_code, lookback_days)
    alerts.check_not_empty(points, on=today, label=job)
    if not points:
        # 주말·휴장일. check_not_empty 가 영업일에만 실패시키므로 여기까지 올 수 있다.
        _log(job=job, batch_id=batch_id, fetched=0, written=0, note="휴장(정상)")
        return 0

    with db.connect() as conn:
        previous = _latest_close(conn, index_code)
        written = db.upsert_index(
            conn, points, provisional=PROVISIONAL[index_code], batch_id=batch_id
        )
        conn.commit()
        alerts.check_staleness(
            db.latest_date(conn, INDEX_TABLE, "index_code", index_code, "trade_date"),
            today=today,
        )

    newest = max(points, key=lambda p: p[1])
    warning = alerts.check_outlier(previous, newest[2], threshold=INDEX_OUTLIER_THRESHOLD)
    _log(job=job, batch_id=batch_id, fetched=len(points), written=written,
         latest_date=newest[1], latest_close=newest[2],
         provisional=PROVISIONAL[index_code], warning=warning)
    return 0


def run_fx(*, now: datetime) -> int:
    job = "fx_daily"
    batch_id = _batch_id(job, now)
    today = now.date()

    point = sources.fetch_fx(today)
    alerts.check_not_empty([point] if point else [], on=today, label=job)
    if point is None:
        _log(job=job, batch_id=batch_id, fetched=0, written=0, note="고시 없음(주말·공휴일)")
        return 0

    with db.connect() as conn:
        previous = _latest_rate(conn)
        written = db.upsert_fx(conn, point, batch_id=batch_id)
        conn.commit()
        alerts.check_staleness(
            db.latest_date(conn, FX_TABLE, "currency_code", "USD", "rate_date"),
            today=today,
        )

    warning = alerts.check_outlier(previous, point[2], threshold=FX_OUTLIER_THRESHOLD)
    _log(job=job, batch_id=batch_id, fetched=1, written=written,
         rate_date=point[1], base_rate=point[2], warning=warning)
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

DEFAULT_LOOKBACK_DAYS = 5   # 평소 수집. 배치가 하루 실패해도 다음 날 메워진다


def run(job_name: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> int:
    if job_name not in JOBS:
        _log(error=f"알 수 없는 배치: {job_name}", available=sorted(JOBS))
        return 2
    now = datetime.now(KST)
    try:
        return JOBS[job_name](now, lookback_days)
    except (alerts.BatchFailure, sources.SourceError) as exc:
        _log(job=job_name, error=str(exc), status="failed")
        return 1
