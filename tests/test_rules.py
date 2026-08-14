# tests/test_rules.py
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from collector import rules

KST = timezone(timedelta(hours=9))


def test_kospi_substitute_holiday_is_not_a_session():
    # 2026-08-17 은 광복절(08-15, 토) 대체휴일. XKRX 실측: 08-14 다음 세션은 08-18.
    assert rules.is_session("KOSPI", date(2026, 8, 17)) is False


def test_spx_same_day_is_a_session():
    # XNYS 는 대체휴일이 없다. 08-17 은 정상 세션.
    assert rules.is_session("SPX", date(2026, 8, 17)) is True


def test_kospi_chuseok_is_not_a_session():
    assert rules.is_session("KOSPI", date(2026, 9, 24)) is False


def test_kospi_saturday_is_not_a_session():
    assert rules.is_session("KOSPI", date(2026, 8, 8)) is False


def test_spx_weekday_is_a_session():
    assert rules.is_session("SPX", date(2026, 8, 6)) is True


def test_last_due_session_on_saturday_run_returns_us_friday():
    # index_spx 는 cron 30 6 * * 2-6. 토요일 06:30 KST 실행분은 미국 금요일
    # 세션(마감 20:00 UTC = 05:00 KST + 유예)을 받는 마지막 수집이다.
    now = datetime(2026, 8, 8, 6, 30, tzinfo=KST)  # 토요일
    assert rules.last_due_session("SPX", now) == date(2026, 8, 7)


def test_last_due_session_none_before_any_session_closes():
    # 아주 이른 시각(00:01 KST 개장 훨씬 전)이라도 전날 세션들은 이미 지났을 수
    # 있으니, 세션이 하나도 마감 전인 경계 상황을 직접 구성한다: 첫 영업일 자정 직후.
    now = datetime(2026, 8, 3, 0, 1, tzinfo=KST)  # 월요일 00:01, 아직 그날 세션 마감 전
    due = rules.last_due_session("SPX", now)
    assert due < date(2026, 8, 3)  # 그날 세션은 아직 안 지났다 (직전 금요일이어야 함)


def test_check_freshness_ok_during_holiday_gap():
    # 마지막 적재일이 08-14(금), 지금이 광복절 연휴 중(08-17)이면 아직 08-18
    # 세션이 마감 전이라 실패가 아니다.
    now = datetime(2026, 8, 17, 16, 0, tzinfo=KST)
    rules.check_freshness("KOSPI", date(2026, 8, 14), now)  # raise 안 하면 통과


def test_check_freshness_fails_when_due_session_missing():
    # 08-18 세션은 15:30 마감 + 30분 유예로 16:00 이후면 있어야 한다.
    # 최신 적재일이 여전히 08-14 면 실패.
    now = datetime(2026, 8, 18, 16, 10, tzinfo=KST)
    with pytest.raises(rules.BatchFailure):
        rules.check_freshness("KOSPI", date(2026, 8, 14), now)


def test_check_freshness_ok_when_no_due_session_yet(monkeypatch):
    # 실제 달력에서 "아직 어떤 세션도 마감 전"인 순간을 재현하려면 캘린더 시작
    # 경계(XKRX 2006-08-07)까지 가야 하고, 거기선 30일 lookback 자체가 범위
    # 밖이라 last_due_session 이 예외를 던진다. 그 계산과 무관하게 check_freshness
    # 의 None 처리 분기만 확인한다.
    monkeypatch.setattr(rules, "last_due_session", lambda market, now: None)
    rules.check_freshness("KOSPI", None, datetime(2026, 8, 17, 16, 0, tzinfo=KST))


def test_check_freshness_fails_on_none_when_due_session_exists():
    with pytest.raises(rules.BatchFailure):
        rules.check_freshness("KOSPI", None, datetime(2026, 8, 18, 16, 10, tzinfo=KST))


def test_outlier_warns_beyond_threshold():
    msg = rules.check_outlier(Decimal(1000), Decimal(1200), threshold=Decimal("0.10"))
    assert msg is not None
    assert "20" in msg


def test_outlier_silent_within_threshold():
    assert rules.check_outlier(Decimal(1000), Decimal(1050),
                                threshold=Decimal("0.10")) is None


def test_outlier_silent_without_previous():
    assert rules.check_outlier(None, Decimal(1050), threshold=Decimal("0.10")) is None


def test_extra_closures_override_calendar():
    """exchange_calendars 가 모르는 한국 휴장일. 3년치 실측에서 XKRX 가 틀린 2일이다.

    캘린더는 개장이라고 하지만 코스피 종가도 환율 고시도 실제로 없었다. 세션으로
    보면 그 날 배치가 데이터 없다고 거짓 실패한다.
    """
    assert rules.is_session("KOSPI", date(2026, 7, 17)) is False   # 제헌절
    assert rules.is_session("KOSPI", date(2026, 6, 3)) is False    # 지방선거
    assert rules.is_session("FX", date(2026, 7, 17)) is False      # FX 도 XKRX 를 쓴다
    # 같은 날 미국은 정상 개장이므로 덮이면 안 된다
    assert rules.is_session("SPX", date(2026, 7, 17)) is True


def test_extra_closures_do_not_touch_normal_sessions():
    assert rules.is_session("KOSPI", date(2026, 7, 16)) is True
    assert rules.is_session("KOSPI", date(2026, 7, 20)) is True
