# tests/test_sources.py
import json
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
    points, skipped = sources.fetch_index("SPX")
    assert points == [("SPX", date(2026, 8, 3), Decimal("5200.12"), "yfinance")]
    assert skipped == 0


def test_parses_multiindex_columns(monkeypatch):
    monkeypatch.setattr(sources.yf, "download",
                        lambda *a, **k: _frame([(date(2026, 8, 3), 5200.12)], multiindex=True))
    points, _ = sources.fetch_index("SPX")
    assert points[0][2] == Decimal("5200.12")


def test_skips_nan_close(monkeypatch):
    """NaN 을 그대로 적재하면 이미 들어간 확정 종가를 덮어쓴다. (설계 §5-1)"""
    monkeypatch.setattr(sources.yf, "download", lambda *a, **k: _frame([
        (date(2026, 8, 3), 5200.12),
        (date(2026, 8, 4), float("nan")),
    ]))
    points, skipped = sources.fetch_index("SPX")
    assert len(points) == 1
    assert points[0][1] == date(2026, 8, 3)
    assert skipped == 1


def test_skips_nan_close_logs_observably(monkeypatch, capsys):
    """설계 §9-1 은 NaN 스킵을 "경고" 로 분류한다 — 조용히 넘어가면 안 된다."""
    monkeypatch.setattr(sources.yf, "download", lambda *a, **k: _frame([
        (date(2026, 8, 3), 5200.12),
        (date(2026, 8, 4), float("nan")),
    ]))
    sources.fetch_index("SPX")

    out = capsys.readouterr().out.strip().splitlines()
    logs = [json.loads(line) for line in out if line]
    assert len(logs) == 1
    entry = logs[0]
    assert entry["event"] == "index_nan_skip"
    assert entry["market"] == "SPX"
    assert entry["trade_date"] == "2026-08-04"
    assert entry["field"] == "close"
    assert entry["reason"]


def test_empty_response_returns_empty_list(monkeypatch):
    monkeypatch.setattr(sources.yf, "download", lambda *a, **k: pd.DataFrame())
    assert sources.fetch_index("SPX") == ([], 0)


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
    points, _ = sources.fetch_index("SPX")
    assert points[0][1] == date(2026, 8, 3)


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


def test_fx_request_error_does_not_leak_authkey(monkeypatch):
    """HTTPError 메시지에 담긴 authkey 쿼리스트링이 SourceError 로 새어나가면 안 된다."""
    secret_url = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON?authkey=SECRET123&searchdate=20260803"

    def fake_get(*a, **k):
        raise sources.requests.HTTPError(f"500 Server Error: Internal Server Error for url: {secret_url}")

    monkeypatch.setattr(sources.requests, "get", fake_get)

    with pytest.raises(sources.SourceError) as exc_info:
        sources.fetch_fx(date(2026, 8, 3))

    exc = exc_info.value
    assert "SECRET123" not in str(exc)
    assert exc.__cause__ is None
    assert "SECRET123" not in str(exc.__context__ or "")


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


# --- 선별 재시도 (스펙 §4-3) -------------------------------------------------
# 테스트가 실제로 잠들면 안 되므로 sources.time.sleep 을 monkeypatch 해서 대기를
# 없애고, 호출 기록으로만 재시도 횟수·대기 시간을 검증한다.


class _FailResp:
    """raise_for_status() 가 HTTP 오류를 내는 응답 흉내."""

    def __init__(self, status_code):
        self.status_code = status_code

    def raise_for_status(self):
        raise sources.requests.HTTPError(f"{self.status_code} error", response=self)


def _no_sleep(monkeypatch):
    sleeps = []
    monkeypatch.setattr(sources.time, "sleep", lambda seconds: sleeps.append(seconds))
    return sleeps


def test_fx_backoff_sequence_is_1_2_4():
    assert [sources._backoff_seconds(a) for a in range(3)] == [1, 2, 4]


def test_fx_retries_5xx_twice_then_succeeds_on_third_call(monkeypatch):
    """5xx 2번 후 성공 -> 3번째 호출에서 값을 반환한다."""
    sleeps = _no_sleep(monkeypatch)
    responses = [_FailResp(500), _FailResp(503), _Resp([_usd()])]
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(sources.requests, "get", fake_get)

    point = sources.fetch_fx(date(2026, 8, 3))

    assert point == ("USD", date(2026, 8, 3), Decimal("1385.20"), "koreaexim")
    assert len(calls) == 3
    # 백오프는 1s, 2s 순서(+jitter). 3번째 시도가 성공해 4s 는 쓰이지 않는다.
    assert len(sleeps) == 2
    assert 1.0 <= sleeps[0] < 1.0 + sources._JITTER_MAX_SECONDS
    assert 2.0 <= sleeps[1] < 2.0 + sources._JITTER_MAX_SECONDS


def test_fx_retries_timeout_three_times_then_raises(monkeypatch):
    """타임아웃 3번 -> SourceError. 4번째 호출은 없다(최대 3회 시도)."""
    sleeps = _no_sleep(monkeypatch)
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        raise sources.requests.Timeout("연결 시간 초과")

    monkeypatch.setattr(sources.requests, "get", fake_get)

    with pytest.raises(sources.SourceError):
        sources.fetch_fx(date(2026, 8, 3))

    assert len(calls) == 3
    assert len(sleeps) == 2


def test_fx_result_3_does_not_retry(monkeypatch):
    """result=3(인증 오류)은 재시도 없이 1회만 호출한다 — 응답은 받았고, 재시도해도
    똑같이 실패해 한도만 태운다."""
    sleeps = _no_sleep(monkeypatch)
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return _Resp([{"result": 3, "cur_unit": "USD"}])

    monkeypatch.setattr(sources.requests, "get", fake_get)

    with pytest.raises(sources.SourceError):
        sources.fetch_fx(date(2026, 8, 3))

    assert len(calls) == 1
    assert len(sleeps) == 0


def test_fx_non_retryable_4xx_calls_once(monkeypatch):
    """429·5xx 가 아닌 4xx(예: 404)는 재시도하지 않는다."""
    sleeps = _no_sleep(monkeypatch)
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return _FailResp(404)

    monkeypatch.setattr(sources.requests, "get", fake_get)

    with pytest.raises(sources.SourceError):
        sources.fetch_fx(date(2026, 8, 3))

    assert len(calls) == 1
    assert len(sleeps) == 0


def test_fx_429_is_retryable(monkeypatch):
    sleeps = _no_sleep(monkeypatch)
    responses = [_FailResp(429), _Resp([_usd()])]
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(sources.requests, "get", fake_get)

    point = sources.fetch_fx(date(2026, 8, 3))

    assert point is not None
    assert len(calls) == 2
    assert len(sleeps) == 1
