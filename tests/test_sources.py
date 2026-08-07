# tests/test_sources.py
from datetime import date
from decimal import Decimal

import pandas as pd
import pytest

from collector import sources


def _frame(rows, multiindex=False):
    """yfinance 응답 흉내. 최근 버전은 컬럼이 MultiIndex 로 오기도 한다."""
    idx = pd.DatetimeIndex([d for d, _ in rows])
    closes = [c for _, c in rows]
    if multiindex:
        cols = pd.MultiIndex.from_tuples([("Close", "^GSPC"), ("Open", "^GSPC")])
        return pd.DataFrame([[c, 0.0] for c in closes], index=idx, columns=cols)
    return pd.DataFrame({"Close": closes, "Open": [0.0] * len(closes)}, index=idx)


def test_parses_flat_columns(monkeypatch):
    monkeypatch.setattr(sources.yf, "download",
                        lambda *a, **k: _frame([(date(2026, 8, 3), 5200.12)]))
    points = sources.fetch_index("SPX")
    assert points == [("SPX", date(2026, 8, 3), Decimal("5200.12"), "yfinance")]


def test_parses_multiindex_columns(monkeypatch):
    monkeypatch.setattr(sources.yf, "download",
                        lambda *a, **k: _frame([(date(2026, 8, 3), 5200.12)], multiindex=True))
    assert sources.fetch_index("SPX")[0][2] == Decimal("5200.12")


def test_skips_nan_close(monkeypatch):
    """NaN 을 그대로 적재하면 이미 들어간 확정 종가를 덮어쓴다. (설계 §5-1)"""
    monkeypatch.setattr(sources.yf, "download", lambda *a, **k: _frame([
        (date(2026, 8, 3), 5200.12),
        (date(2026, 8, 4), float("nan")),
    ]))
    points = sources.fetch_index("SPX")
    assert len(points) == 1
    assert points[0][1] == date(2026, 8, 3)


def test_empty_response_returns_empty_list(monkeypatch):
    monkeypatch.setattr(sources.yf, "download", lambda *a, **k: pd.DataFrame())
    assert sources.fetch_index("SPX") == []


def test_unknown_index_code_raises(monkeypatch):
    with pytest.raises(KeyError):
        sources.fetch_index("NIKKEI")


def test_uses_correct_ticker(monkeypatch):
    seen = {}

    def fake(ticker, **kwargs):
        seen["ticker"] = ticker
        return _frame([(date(2026, 8, 3), 2700.5)])

    monkeypatch.setattr(sources.yf, "download", fake)
    sources.fetch_index("KOSPI")
    assert seen["ticker"] == "^KS11"


def test_trade_date_is_not_shifted(monkeypatch):
    """거래소 현지 날짜를 그대로 쓴다. KST 변환 금지. (설계 §4-1)"""
    monkeypatch.setattr(sources.yf, "download",
                        lambda *a, **k: _frame([(date(2026, 8, 3), 5200.12)]))
    assert sources.fetch_index("SPX")[0][1] == date(2026, 8, 3)
