# tests/test_jobs.py
"""소스 -> 판정 -> 적재 배선 검증 (jobs.py). 네트워크는 sources.fetch_* 를
monkeypatch 해서 안 부른다.

jobs.py 는 db.connect() 로 새 연결을 열고 그 안에서 conn.commit() 을 직접
호출한다. conftest 의 conn 픽스처(롤백)만으로는 이 내부 커밋을 막을 수 없어서,
_UncommittedConn 으로 감싸 커밋·클로즈를 무력화한 뒤 db.connect 를 그 래퍼를
돌려주도록 monkeypatch 한다. 격리는 여전히 conn 픽스처의 롤백이 담당한다 —
그래서 실DB 의 SPX/KOSPI 실데이터를 안전하게 그대로 재사용할 수 있다.

MARKET_CLOSED/FAILURE 두 시나리오는 latest_stored 를 결정론적으로 통제해야
해서(실SPX/KOSPI 는 이미 최신까지 쌓여 있어 FAILURE 를 재현 못 할 수 있다)
실데이터가 전혀 없는 'JOBTEST' 코드를 alerts.CALENDARS 등에 임시로 끼워 넣는다.
"""
import json
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from collector import alerts, db, jobs, sources
from collector.config import FX_TABLE, INDEX_TABLE, KST

FUTURE = date(2099, 1, 1)  # 실DB 어디에도 없을 미래 날짜. 충돌 걱정 없이 쓴다.


class _UncommittedConn:
    """진짜 conn 을 감싸 실제 커밋·클로즈를 막는다. cursor() 는 그대로 넘긴다."""

    def __init__(self, real):
        self._real = real

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def commit(self):
        pass  # 진짜 커밋 금지 — conn 픽스처가 테스트 끝에 롤백한다

    def cursor(self, *a, **kw):
        return self._real.cursor(*a, **kw)


@pytest.fixture
def job_conn(conn, monkeypatch):
    """jobs.py 안의 db.connect() 를 conn 픽스처(롤백)로 바꿔치기한다."""
    monkeypatch.setattr(db, "connect", lambda: _UncommittedConn(conn))
    return conn


@pytest.fixture
def jobtest_route(monkeypatch):
    """실데이터가 없는 index_code 'JOBTEST' 를 임시로 끼워 넣는다. XNYS 캘린더를
    재사용하므로 last_due_session 이 항상 결정 가능한 값을 돌려준다.
    PROVISIONAL 은 일부러 안 넣는다 — points 가 빈 경우에만 쓰는 이 두 시나리오
    (MARKET_CLOSED/FAILURE)에서는 run_index 가 그 값을 아예 읽지 않는다."""
    monkeypatch.setitem(alerts.CALENDARS, "JOBTEST", "XNYS")
    monkeypatch.setitem(alerts.AVAILABILITY_GRACE, "JOBTEST", timedelta(minutes=30))
    monkeypatch.setitem(
        jobs.JOBS, "index_jobtest",
        lambda now, days: jobs.run_index("JOBTEST", now=now, lookback_days=days),
    )


def _log_lines(capsys):
    out = capsys.readouterr().out.strip().splitlines()
    return [json.loads(line) for line in out if line]


def _raise_source_error(code, days):
    raise sources.SourceError("네트워크 끊김")


# --- 정상 수집 -------------------------------------------------------------

def test_success_writes_row_and_exits_zero(job_conn, monkeypatch, capsys):
    point = ("SPX", FUTURE, Decimal("5000.00"), "yfinance")
    monkeypatch.setattr(sources, "fetch_index", lambda code, days: ([point], 0))

    rc = jobs.run_index("SPX", now=datetime.now(KST), lookback_days=1)

    assert rc == 0
    log = _log_lines(capsys)[-1]
    assert log["status"] == "success"

    with job_conn.cursor() as cur:
        cur.execute(
            f"SELECT close_value FROM {INDEX_TABLE} WHERE index_code='SPX' AND trade_date=%s",
            (FUTURE,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == Decimal("5000.00")


def test_run_fx_success_writes_row(job_conn, monkeypatch, capsys):
    monkeypatch.setattr(jobs, "FX_ENABLED", True)
    point = ("USD", FUTURE, Decimal("1300.00"), "test")
    monkeypatch.setattr(sources, "fetch_fx", lambda rate_date: point)

    rc = jobs.run_fx(now=datetime.now(KST))

    assert rc == 0
    log = _log_lines(capsys)[-1]
    assert log["status"] == "success"

    with job_conn.cursor() as cur:
        cur.execute(
            f"SELECT base_rate FROM {FX_TABLE} WHERE currency_code='USD' AND rate_date=%s",
            (FUTURE,),
        )
        row = cur.fetchone()
    assert row is not None
    assert row[0] == Decimal("1300.00")


# --- MARKET_CLOSED / FAILURE ------------------------------------------------

def test_market_closed_when_zero_fetched_but_already_fresh(
    job_conn, jobtest_route, monkeypatch, capsys
):
    now = datetime.now(KST)
    due = alerts.last_due_session("JOBTEST", now)
    assert due is not None  # XNYS 는 이력이 충분해 늘 있어야 정상

    # due 세션까지 이미 적재돼 있다고 시딩 (커밋은 안 된다 — job_conn 이 막는다)
    db.upsert_index(job_conn, [("JOBTEST", due, Decimal("1.00"), "seed")],
                    provisional=False, batch_id="seed")

    monkeypatch.setattr(sources, "fetch_index", lambda code, days: ([], 0))

    rc = jobs.run("index_jobtest", lookback_days=1)

    assert rc == 0
    log = _log_lines(capsys)[-1]
    assert log["status"] == "market_closed"


def test_failure_when_due_session_missing(job_conn, jobtest_route, monkeypatch, capsys):
    # 아무것도 시딩하지 않는다 -> latest_stored 는 항상 None (JOBTEST 는 실데이터가 없다)
    monkeypatch.setattr(sources, "fetch_index", lambda code, days: ([], 0))

    rc = jobs.run("index_jobtest", lookback_days=1)

    assert rc == 1
    log = _log_lines(capsys)[-1]
    assert log["status"] == "failure"
    assert "JOBTEST" in log["error"]


def test_source_error_becomes_failure_with_message(monkeypatch, capsys):
    # fetch_index 가 db.connect() 보다 먼저 호출되므로 DB 는 아예 안 건드린다.
    monkeypatch.setattr(sources, "fetch_index", _raise_source_error)

    rc = jobs.run("index_spx", lookback_days=1)

    assert rc == 1
    log = _log_lines(capsys)[-1]
    assert log["status"] == "failure"
    assert "네트워크 끊김" in log["error"]


def test_unknown_job_returns_2(capsys):
    rc = jobs.run("no_such_job")

    assert rc == 2
    log = _log_lines(capsys)[-1]
    assert "no_such_job" in log["error"]


# --- 멱등성 / PROVISIONAL ----------------------------------------------------

def test_running_twice_same_day_does_not_duplicate(job_conn, monkeypatch, capsys):
    point = ("KOSPI", FUTURE, Decimal("2700.00"), "yfinance")
    monkeypatch.setattr(sources, "fetch_index", lambda code, days: ([point], 0))
    now = datetime.now(KST)

    jobs.run_index("KOSPI", now=now, lookback_days=1)
    jobs.run_index("KOSPI", now=now, lookback_days=1)

    with job_conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {INDEX_TABLE} WHERE index_code='KOSPI' AND trade_date=%s",
            (FUTURE,),
        )
        assert cur.fetchone()[0] == 1


def test_run_index_uses_provisional_flag_per_index_code(job_conn, monkeypatch, capsys):
    monkeypatch.setattr(
        sources, "fetch_index",
        lambda code, days: ([(code, FUTURE, Decimal("1.00"), "yfinance")], 0),
    )
    now = datetime.now(KST)

    jobs.run_index("SPX", now=now, lookback_days=1)
    jobs.run_index("KOSPI", now=now, lookback_days=1)

    with job_conn.cursor() as cur:
        cur.execute(
            f"SELECT index_code, is_provisional FROM {INDEX_TABLE} "
            f"WHERE trade_date=%s AND index_code IN ('SPX','KOSPI')",
            (FUTURE,),
        )
        rows = dict(cur.fetchall())

    assert rows["SPX"] is False
    assert rows["KOSPI"] is True


# --- FX_ENABLED 스위치 -------------------------------------------------------

def test_run_fx_skipped_when_disabled(monkeypatch, capsys):
    monkeypatch.setattr(jobs, "FX_ENABLED", False)
    calls = []
    monkeypatch.setattr(sources, "fetch_fx", lambda rate_date: calls.append(rate_date))

    rc = jobs.run_fx(now=datetime.now(KST))

    assert rc == 0
    assert calls == []  # sources.fetch_fx 가 호출되지 않았다
    log = _log_lines(capsys)[-1]
    assert log["status"] == "skipped"


def test_run_fx_runs_normally_when_enabled(job_conn, monkeypatch, capsys):
    monkeypatch.setattr(jobs, "FX_ENABLED", True)
    point = ("USD", FUTURE, Decimal("1300.00"), "test")
    monkeypatch.setattr(sources, "fetch_fx", lambda rate_date: point)

    rc = jobs.run_fx(now=datetime.now(KST))

    assert rc == 0
    log = _log_lines(capsys)[-1]
    assert log["status"] == "success"


# --- NaN 스킵 요약 로그 -------------------------------------------------------

def test_run_index_summary_log_includes_skipped_count(job_conn, monkeypatch, capsys):
    point = ("SPX", FUTURE, Decimal("5000.00"), "yfinance")
    monkeypatch.setattr(sources, "fetch_index", lambda code, days: ([point], 3))

    jobs.run_index("SPX", now=datetime.now(KST), lookback_days=1)

    log = _log_lines(capsys)[-1]
    assert log["skipped"] == 3


# --- fx_backfill -------------------------------------------------------------
# 실DB 어디에도 없을 미래 평일 구간을 쓴다. 09(금) 10(토) 11(일) 12(월) 13(화)
# 14(수) 15(목) 16(금) -> 평일만 골라내면 09,12,13,14,15,16 여섯 날짜다.

_BF_NOW = datetime(2099, 1, 16, tzinfo=KST)
_BF_DAYS = 7
_BF_WEEKDAYS = [date(2099, 1, 9), date(2099, 1, 12), date(2099, 1, 13),
               date(2099, 1, 14), date(2099, 1, 15), date(2099, 1, 16)]
_BF_WEEKEND = [date(2099, 1, 10), date(2099, 1, 11)]


def _bf_point(d, rate="1400.00"):
    return ("USD", d, Decimal(rate), "koreaexim")


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(jobs.time, "sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def test_fx_backfill_skips_already_loaded_dates(job_conn, monkeypatch, no_sleep, capsys):
    """이미 적재된 날짜는 fetch_fx 를 호출하지 않는다 -- 재개 가능성의 핵심."""
    monkeypatch.setattr(jobs, "FX_ENABLED", True)
    seeded = {date(2099, 1, 12), date(2099, 1, 13)}
    for d in seeded:
        db.upsert_fx(job_conn, _bf_point(d), batch_id="seed")

    calls = []

    def fake_fetch_fx(d):
        calls.append(d)
        return _bf_point(d)

    monkeypatch.setattr(sources, "fetch_fx", fake_fetch_fx)

    rc = jobs.run_fx_backfill(now=_BF_NOW, days=_BF_DAYS)

    assert rc == 0
    assert set(calls) == set(_BF_WEEKDAYS) - seeded
    # 주말은 대상 자체가 아니다
    assert not (set(calls) & set(_BF_WEEKEND))

    log = _log_lines(capsys)[-1]
    assert log["status"] == "success"
    assert log["already_loaded"] == 2
    assert log["attempted"] == 4
    assert log["loaded"] == 4


def test_fx_backfill_none_response_is_skipped_and_continues(job_conn, monkeypatch, no_sleep, capsys):
    """None(고시 없음)은 건너뛰고 계속 진행한다 -- 실패가 아니다."""
    monkeypatch.setattr(jobs, "FX_ENABLED", True)
    holiday = date(2099, 1, 13)

    def fake_fetch_fx(d):
        return None if d == holiday else _bf_point(d)

    monkeypatch.setattr(sources, "fetch_fx", fake_fetch_fx)

    rc = jobs.run_fx_backfill(now=_BF_NOW, days=_BF_DAYS)

    assert rc == 0
    log = _log_lines(capsys)[-1]
    assert log["status"] == "success"
    assert log["attempted"] == 6
    assert log["loaded"] == 5
    assert log["no_data"] == 1

    with job_conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {FX_TABLE} WHERE currency_code='USD' AND rate_date=%s",
            (holiday,),
        )
        assert cur.fetchone()[0] == 0


def test_fx_backfill_rate_limit_stops_immediately_and_exits_zero(
    job_conn, monkeypatch, no_sleep, capsys
):
    """RateLimitError 를 만나면 즉시 멈추고 종료코드 0, 그때까지 받은 건 DB 에 남는다."""
    monkeypatch.setattr(jobs, "FX_ENABLED", True)
    stop_at = date(2099, 1, 13)  # 세 번째로 호출될 날짜
    calls = []

    def fake_fetch_fx(d):
        calls.append(d)
        if d == stop_at:
            raise sources.RateLimitError("일일제한 초과")
        return _bf_point(d)

    monkeypatch.setattr(sources, "fetch_fx", fake_fetch_fx)

    rc = jobs.run_fx_backfill(now=_BF_NOW, days=_BF_DAYS)

    assert rc == 0
    assert calls == [date(2099, 1, 9), date(2099, 1, 12), date(2099, 1, 13)]  # 그 이후는 안 부른다

    log = _log_lines(capsys)[-1]
    assert log["status"] == "rate_limited"
    assert log["loaded"] == 2
    assert log["stopped_reason"]

    with job_conn.cursor() as cur:
        cur.execute(
            f"SELECT count(*) FROM {FX_TABLE} WHERE currency_code='USD' "
            f"AND rate_date = ANY(%s)",
            ([date(2099, 1, 9), date(2099, 1, 12)],),
        )
        assert cur.fetchone()[0] == 2  # 중단 전까지 받은 건 남아 있다


def test_fx_backfill_other_source_error_exits_one(job_conn, monkeypatch, no_sleep, capsys):
    """RateLimitError 가 아닌 SourceError 는 중단하고 실패(종료코드 1)다."""
    monkeypatch.setattr(jobs, "FX_ENABLED", True)
    fail_at = date(2099, 1, 12)

    def fake_fetch_fx(d):
        if d == fail_at:
            raise sources.SourceError("인증코드 오류")
        return _bf_point(d)

    monkeypatch.setattr(sources, "fetch_fx", fake_fetch_fx)

    rc = jobs.run_fx_backfill(now=_BF_NOW, days=_BF_DAYS)

    assert rc == 1
    log = _log_lines(capsys)[-1]
    assert log["status"] == "failure"
    assert "인증코드 오류" in log["error"]


def test_fx_backfill_sleep_is_injectable(job_conn, monkeypatch, no_sleep, capsys):
    """호출 사이 간격은 실제로 잠들지 않고 주입 가능해야 한다."""
    monkeypatch.setattr(jobs, "FX_ENABLED", True)
    monkeypatch.setattr(sources, "fetch_fx", lambda d: _bf_point(d))

    jobs.run_fx_backfill(now=_BF_NOW, days=_BF_DAYS, sleep_seconds=0.2)

    assert no_sleep == [0.2] * len(_BF_WEEKDAYS)


def test_fx_backfill_skipped_when_fx_disabled(monkeypatch, capsys):
    monkeypatch.setattr(jobs, "FX_ENABLED", False)
    calls = []
    monkeypatch.setattr(sources, "fetch_fx", lambda d: calls.append(d))

    rc = jobs.run_fx_backfill(now=_BF_NOW, days=_BF_DAYS)

    assert rc == 0
    assert calls == []
    log = _log_lines(capsys)[-1]
    assert log["status"] == "skipped"
