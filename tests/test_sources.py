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


def _jpy(rate="895.51"):
    return {"result": 1, "cur_unit": "JPY(100)", "cur_nm": "일본 옌", "deal_bas_r": rate}


def _by_code(points):
    return {code: (code, rate_date, rate, source) for code, rate_date, rate, source in points}


def test_fx_parses_comma_separated_rate(monkeypatch):
    """deal_bas_r 은 '1,385.20' 같은 콤마 포함 문자열이다. (스펙 §1-2 함정①)"""
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp([_usd(), _jpy()]))
    points = sources.fetch_fx(date(2026, 8, 3))
    usd = _by_code(points)["USD"]
    assert usd == ("USD", date(2026, 8, 3), Decimal("1385.20"), "koreaexim")
    assert isinstance(usd[2], Decimal)


def test_fx_jpy_100_unit_is_normalized_to_per_1_jpy(monkeypatch):
    """cur_unit 'JPY(100)' 은 100엔당 값이다. 895.51 -> 1엔당 8.9551 로 나눠 저장한다.
    ÷100 을 빠뜨리면 환율이 100배로 잘못 저장된다. (스펙 §부록 A) 이 테스트가 이번
    작업의 핵심이다."""
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp([_usd(), _jpy("895.51")]))
    points = sources.fetch_fx(date(2026, 8, 3))
    jpy = _by_code(points)["JPY"]
    assert jpy == ("JPY", date(2026, 8, 3), Decimal("8.9551"), "koreaexim")
    assert isinstance(jpy[2], Decimal)


def test_fx_usd_is_not_divided(monkeypatch):
    """USD 는 나누는 단위가 1이라 원래 값 그대로 저장돼야 한다."""
    monkeypatch.setattr(sources.requests, "get",
                        lambda *a, **k: _Resp([_usd("1,418.80"), _jpy()]))
    points = sources.fetch_fx(date(2026, 8, 3))
    assert _by_code(points)["USD"][2] == Decimal("1418.80")


def test_fx_filters_out_other_currencies(monkeypatch):
    """응답에 20여 개 통화가 섞여 온다. 설정 안 된 통화(EUR)는 결과에 안 담긴다. (스펙 §1-2 함정③)"""
    payload = [
        _jpy("950.00"),
        _usd(),
        {"result": 1, "cur_unit": "EUR", "deal_bas_r": "1,500.00"},
    ]
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp(payload))
    points = sources.fetch_fx(date(2026, 8, 3))
    assert _by_code(points)["USD"][2] == Decimal("1385.20")
    assert set(_by_code(points)) == {"USD", "JPY"}


def test_fx_missing_configured_currency_raises(monkeypatch):
    """설정된 통화(JPY) 가 응답에 없으면 조용히 빠지지 않고 SourceError 로 실패한다."""
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp([_usd()]))
    with pytest.raises(sources.SourceError):
        sources.fetch_fx(date(2026, 8, 3))


def test_fx_empty_array_returns_empty_list(monkeypatch):
    """주말·공휴일은 고시 자체가 없어 빈 배열이 온다. 이것만으로는 실패가 아니다."""
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp([]))
    assert sources.fetch_fx(date(2026, 8, 2)) == []


@pytest.mark.parametrize("code", [2, 3, 4])
def test_fx_nonzero_result_code_raises(monkeypatch, code):
    """인증키 오류·한도 초과도 HTTP 200 으로 온다. result 코드를 봐야 한다. (함정②)"""
    monkeypatch.setattr(sources.requests, "get",
                        lambda *a, **k: _Resp([{"result": code, "cur_unit": "USD"}]))
    with pytest.raises(sources.SourceError):
        sources.fetch_fx(date(2026, 8, 3))


def test_fx_result_4_raises_rate_limit_error(monkeypatch):
    """일일제한 초과(result=4)는 백필이 "오늘은 여기까지"로 구분해야 하므로
    SourceError 가 아니라 그 하위 타입인 RateLimitError 여야 한다."""
    monkeypatch.setattr(sources.requests, "get",
                        lambda *a, **k: _Resp([{"result": 4, "cur_unit": "USD"}]))
    with pytest.raises(sources.RateLimitError):
        sources.fetch_fx(date(2026, 8, 3))


def test_fx_result_3_is_not_rate_limit_error(monkeypatch):
    """result=3(인증 오류)은 SourceError 이지만 RateLimitError 는 아니다 — 백필이
    이 둘을 다르게 처리해야 한다."""
    monkeypatch.setattr(sources.requests, "get",
                        lambda *a, **k: _Resp([{"result": 3, "cur_unit": "USD"}]))
    with pytest.raises(sources.SourceError) as exc_info:
        sources.fetch_fx(date(2026, 8, 3))
    assert not isinstance(exc_info.value, sources.RateLimitError)


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
        return _Resp([_usd(), _jpy()])

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
    responses = [_FailResp(500), _FailResp(503), _Resp([_usd(), _jpy()])]
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(sources.requests, "get", fake_get)

    points = sources.fetch_fx(date(2026, 8, 3))

    assert _by_code(points)["USD"] == ("USD", date(2026, 8, 3), Decimal("1385.20"), "koreaexim")
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
    responses = [_FailResp(429), _Resp([_usd(), _jpy()])]
    calls = []

    def fake_get(*a, **k):
        calls.append(1)
        return responses[len(calls) - 1]

    monkeypatch.setattr(sources.requests, "get", fake_get)

    points = sources.fetch_fx(date(2026, 8, 3))

    assert points != []
    assert len(calls) == 2
    assert len(sleeps) == 1


# --- 적재 불가 값 차단 (P1) --------------------------------------------------
# 로컬 DB 는 CHECK 제약이 막아주지만 적재 대상인 vaultdb 에는 그 제약이 없다.
# 0·음수·Infinity 가 통과하면 UPSERT 라 이미 들어가 있던 정상값을 덮어쓴다.


@pytest.mark.parametrize("bad", [0.0, -1.5, float("inf")])
def test_index_rejects_unloadable_close(monkeypatch, bad):
    # inf 는 quantize 단계에서 먼저 걸린다 — 어느 쪽이든 배치가 서면 목적은 같다
    monkeypatch.setattr(sources.yf, "download",
                        lambda *a, **k: _frame([(date(2026, 8, 3), bad)]))
    with pytest.raises(sources.SourceError, match="적재할 수 없는 값|숫자로 못 읽음"):
        sources.fetch_index("SPX")


@pytest.mark.parametrize("bad", ["0", "-1385.20"])
def test_fx_rejects_unloadable_rate(monkeypatch, bad):
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp([_usd(bad), _jpy()]))
    with pytest.raises(sources.SourceError, match="적재할 수 없는 값"):
        sources.fetch_fx(date(2026, 8, 3))


def test_fx_malformed_rate_becomes_source_error(monkeypatch):
    """숫자가 아닌 값이 와도 traceback 이 아니라 SourceError 여야 로그 규약이 산다."""
    broken = {**_usd(), "deal_bas_r": None}
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp([broken, _jpy()]))
    with pytest.raises(sources.SourceError, match="deal_bas_r"):
        sources.fetch_fx(date(2026, 8, 3))


def test_fx_non_list_payload_becomes_source_error(monkeypatch):
    """인증 실패 시 JSON 대신 다른 모양이 오는 사례가 있다."""
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: _Resp({"error": "bad key"}))
    with pytest.raises(sources.SourceError, match="응답 형식"):
        sources.fetch_fx(date(2026, 8, 3))


# --- yfinance 재시도 (설계 §9-3) ---------------------------------------------

def test_index_retries_then_raises_source_error(monkeypatch):
    """설계가 규정한 3회 재시도. 끝까지 실패하면 traceback 이 아니라 SourceError."""
    sleeps = _no_sleep(monkeypatch)
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise ConnectionError("yahoo 응답 없음")

    monkeypatch.setattr(sources.yf, "download", boom)

    with pytest.raises(sources.SourceError, match="yfinance 호출 실패"):
        sources.fetch_index("SPX")

    assert len(calls) == 3
    assert [round(s) for s in sleeps] == [2, 4]  # 2 -> 4 (+jitter), 3번째는 안 잠


def test_index_succeeds_on_retry(monkeypatch):
    _no_sleep(monkeypatch)
    calls = []

    def flaky(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("느림")
        return _frame([(date(2026, 8, 3), 5200.12)])

    monkeypatch.setattr(sources.yf, "download", flaky)

    points, _ = sources.fetch_index("SPX")
    assert points[0][2] == Decimal("5200.12")
    assert len(calls) == 2


def test_index_malformed_frame_becomes_source_error(monkeypatch):
    """Close 컬럼이 없는 응답. 형식이 바뀌어도 로그 규약을 깨지 않는다."""
    monkeypatch.setattr(sources.yf, "download",
                        lambda *a, **k: pd.DataFrame({"Open": [1.0]},
                                                     index=pd.DatetimeIndex([date(2026, 8, 3)])))
    with pytest.raises(sources.SourceError, match="응답 형식"):
        sources.fetch_index("SPX")
