"""소스 -> 판정 -> 적재 배선. 로그는 stdout JSON 한 줄. (설계 §9-2)"""
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from . import alerts, db, sources
from .config import FX_ENABLED, FX_TABLE, INDEX_TABLE, KST, PROVISIONAL
from .logs import log as _log

FX_BACKFILL_SLEEP_SECONDS = 0.2  # 한도를 급하게 태우지 않기 위한 호출 간 간격
FX_BACKFILL_PROGRESS_EVERY = 50  # 이 건수마다 진행 로그 + 커밋

INDEX_OUTLIER_THRESHOLD = Decimal("0.10")  # 지수는 환율보다 변동성이 크다 (설계 §9-1)
FX_OUTLIER_THRESHOLD = Decimal("0.05")


class JobStatus(str, Enum):
    SUCCESS = "success"
    MARKET_CLOSED = "market_closed"  # 알림 대상 아님. 종료코드 0
    SKIPPED = "skipped"  # FX_ENABLED=false. 알림 대상 아님. 종료코드 0
    FAILURE = "failure"  # 종료코드 1
    RATE_LIMITED = "rate_limited"  # 일일 한도 초과로 중단. "오늘 몫은 여기까지". 종료코드 0


def _batch_id(job: str, now: datetime) -> str:
    return f"{job}-{now.isoformat()}"


def run_index(index_code: str, *, now: datetime, lookback_days: int = 5) -> int:
    job = f"index_{index_code.lower()}"
    batch_id = _batch_id(job, now)

    points, skipped = sources.fetch_index(index_code, lookback_days)

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
        "fetched": len(points), "written": written, "skipped": skipped,
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

    if not FX_ENABLED:
        _log(job=job, batch_id=batch_id, status=JobStatus.SKIPPED.value,
             reason="FX_ENABLED=false (EXIM_API_KEY 미발급)")
        return 0

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


def run_fx_backfill(*, now: datetime, days: int, sleep_seconds: float = FX_BACKFILL_SLEEP_SECONDS) -> int:
    """오늘로부터 days 일 전까지 평일마다 fetch_fx 를 하나씩 호출해 채운다.

    이미 적재된 날짜는 DB 에서 한 번에 읽어 호출 자체를 건너뛴다 -> 중단 후
    재실행하면 이어서 진행된다. RateLimitError 는 실패가 아니라 "오늘 몫은
    여기까지"라 종료코드 0으로 끝낸다.
    """
    job = "fx_backfill"
    batch_id = _batch_id(job, now)

    if not FX_ENABLED:
        _log(job=job, batch_id=batch_id, status=JobStatus.SKIPPED.value,
             reason="FX_ENABLED=false (EXIM_API_KEY 미발급)")
        return 0

    end = now.date()
    start = end - timedelta(days=days)
    target_dates = [
        d for d in (start + timedelta(days=i) for i in range((end - start).days + 1))
        if d.weekday() < 5  # 월~금
    ]

    with db.connect() as conn:
        existing = _existing_fx_dates(conn)
        pending = [d for d in target_dates if d not in existing]
        already_loaded = len(target_dates) - len(pending)

        attempted = loaded = no_data = 0
        stopped_reason: str | None = None
        last_date: date | None = None

        for d in pending:
            try:
                point = sources.fetch_fx(d)
            except sources.RateLimitError as exc:
                stopped_reason = f"일일 호출 한도 초과: {exc}. 재실행하면 이어서 진행됩니다."
                break
            except sources.SourceError as exc:
                conn.commit()
                _log(job=job, batch_id=batch_id, status=JobStatus.FAILURE.value,
                     error=str(exc), target_weekdays=len(target_dates),
                     already_loaded=already_loaded, attempted=attempted,
                     loaded=loaded, no_data=no_data, last_date=last_date)
                return 1

            attempted += 1
            last_date = d
            if point is None:
                no_data += 1
            else:
                db.upsert_fx(conn, point, batch_id=batch_id)
                loaded += 1

            if attempted % FX_BACKFILL_PROGRESS_EVERY == 0:
                conn.commit()
                _log(job=job, batch_id=batch_id, status="progress",
                     attempted=attempted, loaded=loaded, no_data=no_data,
                     remaining=len(pending) - attempted, last_date=d)

            time.sleep(sleep_seconds)

        conn.commit()

    status = JobStatus.RATE_LIMITED if stopped_reason else JobStatus.SUCCESS
    _log(job=job, batch_id=batch_id, status=status.value,
         target_weekdays=len(target_dates), already_loaded=already_loaded,
         attempted=attempted, loaded=loaded, no_data=no_data,
         stopped_reason=stopped_reason, last_date=last_date)
    return 0


def _existing_fx_dates(conn) -> set[date]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT rate_date FROM {FX_TABLE} WHERE currency_code = 'USD'")
        return {row[0] for row in cur.fetchall()}


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
    "fx_backfill": lambda now, days: run_fx_backfill(now=now, days=days),
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
