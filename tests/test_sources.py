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


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _usd(rate="1,385.20"):
    return {"result": 1, "cur_unit": "USD", "cur_nm": "미국 달러", "deal_bas_r": rate}


def test_fx_parses_comma_separated_rate(monkeypatch):
    """deal_bas_r 은 '1,385.20' 같은 콤마 포함 문자열이다. (스펙 §1-2 함정①)"""
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp([_usd()]))
    point = sources.fetch_fx(date(2026, 8, 3))
    assert point == ("USD", date(2026, 8, 3), Decimal("1385.20"), "koreaexim")
    assert isinstance(point[2], Decimal)


def test_fx_filters_out_other_currencies(monkeypatch):
    """응답에 40여 개 통화가 섞여 온다. (스펙 §1-2 함정③)"""
    payload = [
        {"result": 1, "cur_unit": "JPY(100)", "deal_bas_r": "950.00"},
        _usd(),
        {"result": 1, "cur_unit": "EUR", "deal_bas_r": "1,500.00"},
    ]
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp(payload))
    assert sources.fetch_fx(date(2026, 8, 3))[2] == Decimal("1385.20")


def test_fx_empty_array_returns_none(monkeypatch):
    """주말·공휴일은 고시 자체가 없어 빈 배열이 온다. 이것만으로는 실패가 아니다."""
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp([]))
    assert sources.fetch_fx(date(2026, 8, 2)) is None


@pytest.mark.parametrize("code", [2, 3, 4])
def test_fx_nonzero_result_code_raises(monkeypatch, code):
    """인증키 오류·한도 초과도 HTTP 200 으로 온다. result 코드를 봐야 한다. (함정②)"""
    monkeypatch.setattr(sources.requests, "get",
                        lambda *a, **k: _Resp([{"result": code, "cur_unit": "USD"}]))
    with pytest.raises(sources.SourceError):
        sources.fetch_fx(date(2026, 8, 3))


def test_fx_missing_usd_raises(monkeypatch):
    monkeypatch.setattr(sources.requests, "get",
                        lambda *a, **k: _Resp([{"result": 1, "cur_unit": "EUR",
                                                "deal_bas_r": "1,500.00"}]))
    with pytest.raises(sources.SourceError):
        sources.fetch_fx(date(2026, 8, 3))


def test_fx_calls_current_domain(monkeypatch):
    """구 도메인 www.koreaexim.go.kr 은 2026-04-30 종료됐다. (스펙 §1-1)"""
    seen = {}

    def fake(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs.get("params", {})
        return _Resp([_usd()])

    monkeypatch.setattr(sources.requests, "get", fake)
    sources.fetch_fx(date(2026, 8, 3))
    assert seen["url"].startswith("https://oapi.koreaexim.go.kr/")
    assert seen["params"]["searchdate"] == "20260803"
    assert seen["params"]["data"] == "AP01"
