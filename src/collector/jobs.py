"""소스 -> 판정 -> 적재 배선. 로그는 stdout JSON 한 줄. (설계 §9-2)"""
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

from . import alerts, db, sources
from .config import FX_CURRENCY_CODES, FX_ENABLED, FX_TABLE, INDEX_TABLE, KST
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
            written = db.upsert_index(conn, points, batch_id=batch_id)
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
            latest_date=newest[1], latest_close=newest[2], warning=warning,
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

    rows = sources.fetch_fx(today)

    with db.connect() as conn:
        # 통화별로 upsert 한다. USD 는 오는데 JPY 만 안 오는 상황을 신선도 검사가
        # 잡아야 하므로, 이번에 안 온 통화도 포함해 FX_CURRENCY_CODES 전체를 검사한다.
        written_by_currency: dict[str, int] = {}
        warnings_by_currency: dict[str, str] = {}
        for code, rate_date, base_rate, source in rows:
            previous = _latest_rate(conn, code)
            written_by_currency[code] = db.upsert_fx(
                conn, (code, rate_date, base_rate, source), batch_id=batch_id
            )
            warning = alerts.check_outlier(previous, base_rate, threshold=FX_OUTLIER_THRESHOLD)
            if warning:
                warnings_by_currency[code] = warning
        if rows:
            conn.commit()

        for code in FX_CURRENCY_CODES:
            latest = db.latest_date(conn, FX_TABLE, "currency_code", code, "rate_date")
            try:
                alerts.check_freshness("FX", latest, now)
            except alerts.BatchFailure as exc:
                raise alerts.BatchFailure(f"{code}: {exc}") from exc

    status = JobStatus.SUCCESS if rows else JobStatus.MARKET_CLOSED
    _log(
        job=job, batch_id=batch_id, status=status.value,
        # 고시가 없는 날은 "없음" 으로 남긴다(성호님 요청). 실패가 아니라 정상이다.
        # 주말뿐 아니라 공휴일도 고시가 없어 같은 문구로 처리한다.
        result="없음" if not rows else None,
        fetched=len(rows), written=written_by_currency,
        rate_date=today if rows else None,
        warnings=warnings_by_currency or None,
    )
    return 0


def run_fx_backfill(*, now: datetime, days: int, sleep_seconds: float = FX_BACKFILL_SLEEP_SECONDS) -> int:
    """오늘로부터 days 일 전까지 평일마다 fetch_fx 를 하나씩 호출해 채운다.

    이미 적재된 (통화, 날짜) 조합은 건너뛴다 -> USD 만 있고 JPY 가 없는 날짜는
    여전히 pending 이라 fetch_fx 가 호출되고, 응답 중 이미 있는 통화(USD)만
    다시 건너뛴다. 날짜만 보고 건너뛰면 USD 가 이미 찬 날짜 전체가 걸러져
    JPY 백필이 한 건도 안 되므로, 판정은 반드시 (통화, 날짜) 단위여야 한다.

    RateLimitError 는 실패가 아니라 "오늘 몫은 여기까지"라 종료코드 0으로 끝낸다.
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
        existing = _existing_fx_pairs(conn)
        pending = [
            d for d in target_dates
            if any((code, d) not in existing for code in FX_CURRENCY_CODES)
        ]
        already_loaded = len(target_dates) - len(pending)

        attempted = loaded = no_data = 0
        stopped_reason: str | None = None
        last_date: date | None = None

        for d in pending:
            try:
                rows = sources.fetch_fx(d)
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
            if not rows:
                no_data += 1
            else:
                for row in rows:
                    code = row[0]
                    if (code, d) in existing:
                        continue  # 이 통화는 이 날짜에 이미 있다 (예: USD)
                    db.upsert_fx(conn, row, batch_id=batch_id)
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


def _existing_fx_pairs(conn) -> set[tuple[str, date]]:
    with conn.cursor() as cur:
        cur.execute(f"SELECT currency_code, rate_date FROM {FX_TABLE}")
        return {(row[0], row[1]) for row in cur.fetchall()}


def _latest_close(conn, index_code: str) -> Decimal | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT close_value FROM {INDEX_TABLE} WHERE index_code = %s "
            f"ORDER BY trade_date DESC LIMIT 1",
            (index_code,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _latest_rate(conn, currency_code: str) -> Decimal | None:
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT base_rate FROM {FX_TABLE} WHERE currency_code = %s "
            f"ORDER BY rate_date DESC LIMIT 1",
            (currency_code,),
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
