"""소스 -> 판정 -> 적재 배선. 로그는 stdout JSON 한 줄. (설계 §9-2)"""
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum

import psycopg

from . import alerts, db, sources
from .config import FX_CURRENCY_CODES, FX_ENABLED, FX_TABLE, INDEX_TABLE, KST
from .logs import log as _log

FX_BACKFILL_SLEEP_SECONDS = 0.2  # 한도를 급하게 태우지 않기 위한 호출 간 간격
FX_BACKFILL_PROGRESS_EVERY = 50  # 이 건수마다 진행 로그 + 커밋

# run_fx 가 되돌아보는 범위. 지수는 매번 최근 5일을 UPSERT 해 저절로 메워지는데,
# 환율은 당일 1건만 받아서 실행이 하루 빠지면 그 날짜가 영영 비었다. 같은 폭으로
# 되돌아보며 없는 날만 채운다. (상시 실행 전환 설계 §4)
FX_LOOKBACK_DAYS = 5

INDEX_OUTLIER_THRESHOLD = Decimal("0.10")  # 지수는 환율보다 변동성이 크다 (설계 §9-1)
FX_OUTLIER_THRESHOLD = Decimal("0.05")


class JobStatus(str, Enum):
    SUCCESS = "success"
    UP_TO_DATE = "up_to_date"  # 받을 게 없다(이미 적재됨·주말·휴장). 알림 대상 아님. 종료코드 0
    MARKET_CLOSED = "market_closed"  # 알림 대상 아님. 종료코드 0
    SKIPPED = "skipped"  # FX_ENABLED=false. 알림 대상 아님. 종료코드 0
    FAILURE = "failure"  # 종료코드 1
    RATE_LIMITED = "rate_limited"  # 일일 한도 초과로 중단. "오늘 몫은 여기까지". 종료코드 0


# batch_id 는 "어느 실행" 이 아니라 "어느 계정" 이 쓴 행인지를 남기는 컬럼이다
# (2026-08-11 성호님 정리). 그래서 접속 계정명(conn.info.user) 을 그대로 넣는다.
# 실행 단위 추적은 stdout 로그의 job + ts 가 한다.


def _up_to_date(market: str, latest_stored: date | None, now: datetime) -> bool:
    """마감·유예가 지난 최신 세션까지 이미 갖고 있는가. 그러면 이번 실행은 할 일이 없다.

    '이미 적재됨' 과 '주말·휴장' 이 한 조건으로 처리된다 — 휴장이면 due 가 직전 영업일을
    가리키고 그 날짜는 이미 갖고 있기 때문이다. 그래서 요일 검사를 따로 두지 않는다.
    (상시 실행 전환 설계 §4)
    """
    due = alerts.last_due_session(market, now)
    if due is None:
        return True  # 아직 아무 세션도 마감·유예를 안 지났다. 받을 게 있을 수 없다
    return latest_stored is not None and latest_stored >= due


def run_index(index_code: str, *, now: datetime, lookback_days: int = 5) -> int:
    job = f"index_{index_code.lower()}"

    with db.connect() as conn:
        # 먼저 DB 를 보고, 받을 게 없으면 외부 호출 없이 끝낸다. 12시간 주기에서는
        # 실행의 절반 이상이 여기서 끝나므로, 이 판정이 fetch 앞에 있어야 의미가 있다.
        latest = db.latest_date(conn, INDEX_TABLE, "index_code", index_code, "trade_date")
        if _up_to_date(index_code, latest, now):
            _log(job=job, status=JobStatus.UP_TO_DATE.value, latest_date=latest)
            return 0

        points, skipped = sources.fetch_index(index_code, lookback_days)

        # 0건이어도 계속 진행한다 — 그래야 이번 실행이 0건이어도 staleness/freshness
        # 판정이 살아있다 (예: 토요일 06:30 실행이 미국 금요일분을 이미 받은 뒤
        # 그 다음 실행에서 0건이 나오는 경우도 최신 적재일 기준으로 계속 검사된다).
        previous = _latest_close(conn, index_code) if points else None
        written = 0
        if points:
            written = db.upsert_index(conn, points, batch_id=conn.info.user)
            conn.commit()
        latest = db.latest_date(conn, INDEX_TABLE, "index_code", index_code, "trade_date")
        alerts.check_freshness(index_code, latest, now)

    status = JobStatus.SUCCESS if points else JobStatus.MARKET_CLOSED
    log_fields = {
        "job": job, "status": status.value,
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


def _pending_fx_dates(conn, now: datetime, lookback_days: int) -> list[date]:
    """최근 lookback_days 안에서 아직 못 받은 고시일. 오래된 날짜부터.

    마감·유예가 지난 날까지만 본다 — 오늘 고시 전에 돌면 오늘은 대상이 아니다.
    요일이 아니라 거래소 캘린더로 거르므로 공휴일에 헛호출하지 않는다.
    판정이 (통화, 날짜) 단위인 이유는 run_fx_backfill 과 같다 — USD 만 있고 JPY 가
    없는 날짜를 날짜만 보고 거르면 JPY 가 영영 안 채워진다.
    """
    due = alerts.last_due_session("FX", now)
    if due is None:
        return []
    start = due - timedelta(days=lookback_days)
    existing = _existing_fx_pairs(conn, start)
    candidates = (start + timedelta(days=i) for i in range((due - start).days + 1))
    return [
        d for d in candidates
        if alerts.is_session("FX", d)
        and any((code, d) not in existing for code in FX_CURRENCY_CODES)
    ]


def run_fx(*, now: datetime, lookback_days: int = FX_LOOKBACK_DAYS) -> int:
    job = "fx_daily"

    if not FX_ENABLED:
        _log(job=job, status=JobStatus.SKIPPED.value,
             reason="FX_ENABLED=false (EXIM_API_KEY 미발급)")
        return 0

    with db.connect() as conn:
        # 받을 게 없으면 외부 호출 없이 끝낸다. 수출입은행은 일일 호출 한도가 있어
        # 헛호출을 아끼는 것이 그대로 이득이다.
        pending = _pending_fx_dates(conn, now, lookback_days)
        if not pending:
            _log(job=job, status=JobStatus.UP_TO_DATE.value,
                 latest_date=db.latest_date(conn, FX_TABLE, "currency_code",
                                            FX_CURRENCY_CODES[0], "rate_date"))
            return 0

        # 통화별로 upsert 한다. USD 는 오는데 JPY 만 안 오는 상황을 신선도 검사가
        # 잡아야 하므로, 이번에 안 온 통화도 포함해 FX_CURRENCY_CODES 전체를 검사한다.
        written_by_currency: dict[str, int] = {}
        warnings_by_currency: dict[str, str] = {}
        fetched = 0
        stopped_reason: str | None = None
        for rate_date in pending:
            try:
                rows = sources.fetch_fx(rate_date)
            except sources.RateLimitError as exc:
                # 실패가 아니라 "오늘 몫은 여기까지". 다음 실행이 이어받는다.
                stopped_reason = f"일일 호출 한도 초과: {exc}"
                break
            fetched += len(rows)
            for code, row_date, base_rate, source in rows:
                previous = _latest_rate(conn, code)
                written_by_currency[code] = written_by_currency.get(code, 0) + db.upsert_fx(
                    conn, (code, row_date, base_rate, source), batch_id=conn.info.user
                )
                warning = alerts.check_outlier(previous, base_rate, threshold=FX_OUTLIER_THRESHOLD)
                if warning:
                    warnings_by_currency[code] = warning
        if fetched:
            conn.commit()

        for code in FX_CURRENCY_CODES:
            latest = db.latest_date(conn, FX_TABLE, "currency_code", code, "rate_date")
            try:
                alerts.check_freshness("FX", latest, now)
            except alerts.BatchFailure as exc:
                raise alerts.BatchFailure(f"{code}: {exc}") from exc

    if stopped_reason:
        status = JobStatus.RATE_LIMITED
    else:
        status = JobStatus.SUCCESS if fetched else JobStatus.MARKET_CLOSED
    _log(
        job=job, status=status.value,
        # 고시가 없는 날은 "없음" 으로 남긴다(성호님 요청). 실패가 아니라 정상이다.
        # 주말뿐 아니라 공휴일도 고시가 없어 같은 문구로 처리한다.
        result="없음" if not fetched else None,
        fetched=fetched, written=written_by_currency,
        rate_date=pending[-1] if fetched else None,
        target_dates=[str(d) for d in pending],
        stopped_reason=stopped_reason,
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

    if not FX_ENABLED:
        _log(job=job, status=JobStatus.SKIPPED.value,
             reason="FX_ENABLED=false (EXIM_API_KEY 미발급)")
        return 0

    end = now.date()
    start = end - timedelta(days=days)
    target_dates = [
        d for d in (start + timedelta(days=i) for i in range((end - start).days + 1))
        if d.weekday() < 5  # 월~금
    ]

    with db.connect() as conn:
        existing = _existing_fx_pairs(conn, start)
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
                _log(job=job, status=JobStatus.FAILURE.value,
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
                    db.upsert_fx(conn, row, batch_id=conn.info.user)
                    loaded += 1

            if attempted % FX_BACKFILL_PROGRESS_EVERY == 0:
                conn.commit()
                _log(job=job, status="progress",
                     attempted=attempted, loaded=loaded, no_data=no_data,
                     remaining=len(pending) - attempted, last_date=d)

            time.sleep(sleep_seconds)

        conn.commit()

    status = JobStatus.RATE_LIMITED if stopped_reason else JobStatus.SUCCESS
    _log(job=job, status=status.value,
         target_weekdays=len(target_dates), already_loaded=already_loaded,
         attempted=attempted, loaded=loaded, no_data=no_data,
         stopped_reason=stopped_reason, last_date=last_date)
    return 0


def _existing_fx_pairs(conn, since: date) -> set[tuple[str, date]]:
    """since 이후에 이미 적재된 (통화, 날짜) 조합. 백필 판정 범위 밖은 읽지 않는다."""
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT currency_code, rate_date FROM {FX_TABLE} WHERE rate_date >= %s",
            (since,),
        )
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
    except psycopg.Error as exc:
        # 예외 타입만 남긴다. psycopg 의 접속 실패 메시지에는 호스트·계정이 들어가고,
        # 잡지 않으면 traceback 이 그대로 stdout 에 찍혀 JSON 한 줄 규약도 깨진다.
        # sources.py 가 authkey 를 감추는 것과 같은 이유다 — 이쪽은 실환경 접속정보다.
        _log(job=job_name, status=JobStatus.FAILURE.value, error=f"DB 오류: {type(exc).__name__}")
        return 1
