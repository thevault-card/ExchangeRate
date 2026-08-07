# tests/test_alerts.py
from datetime import date
from decimal import Decimal

import pytest

from collector import alerts


def test_weekday_is_business_day():
    assert alerts.is_business_day(date(2026, 8, 3)) is True   # 월
    assert alerts.is_business_day(date(2026, 8, 7)) is True   # 금


def test_weekend_is_not_business_day():
    assert alerts.is_business_day(date(2026, 8, 8)) is False  # 토
    assert alerts.is_business_day(date(2026, 8, 9)) is False  # 일


def test_empty_on_business_day_fails():
    with pytest.raises(alerts.BatchFailure):
        alerts.check_not_empty([], on=date(2026, 8, 3), label="fx")


def test_empty_on_weekend_is_ok():
    alerts.check_not_empty([], on=date(2026, 8, 8), label="fx")


def test_non_empty_is_always_ok():
    alerts.check_not_empty([1], on=date(2026, 8, 3), label="fx")


def test_staleness_fails_after_three_business_days():
    with pytest.raises(alerts.BatchFailure):
        alerts.check_staleness(date(2026, 8, 3), today=date(2026, 8, 10))


def test_staleness_ok_within_limit():
    alerts.check_staleness(date(2026, 8, 6), today=date(2026, 8, 7))


def test_staleness_ok_when_never_loaded():
    alerts.check_staleness(None, today=date(2026, 8, 7))


def test_outlier_warns_beyond_threshold():
    msg = alerts.check_outlier(Decimal("1000"), Decimal("1200"), threshold=Decimal("0.10"))
    assert msg is not None
    assert "20" in msg


def test_outlier_silent_within_threshold():
    assert alerts.check_outlier(Decimal("1000"), Decimal("1050"),
                                threshold=Decimal("0.10")) is None


def test_outlier_silent_without_previous():
    assert alerts.check_outlier(None, Decimal("1050"), threshold=Decimal("0.10")) is None
