"""외부에서 값을 가져온다. 여기가 소스 교체 지점이다.

yfinance 는 Yahoo Finance 의 비공식 엔드포인트를 쓰는 라이브러리다. 상용 서비스
전환 시 유료 API(S&P500)와 공공데이터포털(코스피)로 교체하는 것을 전제로 한다.
교체할 때 fetch_index 의 본문만 바뀌고 호출하는 쪽은 그대로다. (설계 §8)
"""
import random
import time
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd
import requests
import yfinance as yf

from .config import CURRENCIES, EXIM_API_KEY, KST, TICKERS
from .logs import log

IndexRow = tuple[str, date, Decimal, str]


class SourceError(RuntimeError):
    """외부 소스가 실패로 응답했을 때. 재시도해도 소용없는 상황을 포함한다."""


class RateLimitError(SourceError):
    """수출입은행 일일 호출 한도 초과(result=4). 백필이 "오늘은 여기까지"를
    "진짜 실패"와 구분할 수 있어야 해서 SourceError 와 별도로 잡을 수 있게 한다."""


def fetch_index(index_code: str, lookback_days: int = 5) -> tuple[list[IndexRow], int]:
    """오늘로부터 lookback_days 일 전까지의 일별 종가 목록과 건너뛴 NaN 건수.

    period= 대신 start= 를 쓴다. yfinance 의 period 는 '5d','1y','max' 같은 정해진
    값만 받아서 '1095d' 를 넘기면 동작하지 않는다. start= 는 임의 기간이 되므로
    평소 수집(5일)과 초기 백필(3년)이 같은 코드로 처리된다.
    """
    ticker = TICKERS[index_code]
    start = datetime.now(KST).date() - timedelta(days=lookback_days)
    df = yf.download(ticker, start=start, interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return [], 0

    closes = df["Close"]
    if hasattr(closes, "columns"):  # MultiIndex 컬럼이면 첫 열이 우리 티커다
        closes = closes.iloc[:, 0]

    points: list[IndexRow] = []
    skipped = 0
    for stamp, value in closes.items():
        if pd.isna(value):
            # None(값 없음)과 NaN(휴장일·장중 미확정 구간)을 한 번에 거른다.
            # 적재하면 확정 종가를 덮어쓴다. 설계 §9-1: 이건 "경고" 대상이라 관측
            # 가능해야 한다 — 조용히 넘어가면 당일 행이 매일 빠져도 아무도 모른다.
            skipped += 1
            log(event="index_nan_skip", market=index_code, trade_date=stamp.date(),
                field="close", reason="NaN close value")
            continue
        # float() 은 금액 정밀도가 깨진다. str() 로 거쳐 Decimal 로 만들고, PostgreSQL
        # numeric 과 같은 반올림 방식(half-up)으로 소수점 2자리에 맞춘다.
        close_value = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        points.append((index_code, stamp.date(), close_value, "yfinance"))
    return points, skipped


FxRow = tuple[str, date, Decimal, str]

# 구 도메인(www.koreaexim.go.kr)은 2026-04-30 서비스 종료. 인터넷 예제 코드 대부분이
# 구 도메인이라 그대로 복붙하면 동작하지 않는다. (스펙 §1-1)
_EXIM_URL = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"

_RESULT_MEANING = {
    2: "데이터코드 오류",
    3: "인증코드 오류 (키 만료 신호)",
    4: "일일제한 초과",
}

# 재시도 정책 (스펙 §4-3 후속 조정): 3회 시도, 백오프 1s -> 2s -> 4s + 작은 jitter.
# 재시도 대상은 타임아웃·커넥션 오류·429·5xx 뿐이다. 그 외(다른 4xx, result 코드
# 오류, USD 없음)는 재시도해도 결과가 같으므로 즉시 실패시킨다.
_RETRY_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1
_JITTER_MAX_SECONDS = 0.25


def _backoff_seconds(attempt: int) -> float:
    """0-based 시도 번호에 대한 백오프 초(jitter 제외). 1, 2, 4 순서다."""
    return _BACKOFF_BASE_SECONDS * (2 ** attempt)


def _is_retryable(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return False


def fetch_fx(rate_date: date) -> list[FxRow]:
    """CURRENCIES 에 설정된 통화 전부의 매매기준율. 고시가 없는 날(주말·공휴일)이면 [].

    응답에는 23개 통화가 한 번에 오므로(실측), 설정된 통화를 몇 개로 늘려도 API
    호출은 늘지 않는다. 설정된 통화 중 응답에 없는 것이 있으면 조용히 빠지지
    않고 SourceError 로 실패시킨다.
    """
    error_type: str | None = None
    rows = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = requests.get(
                _EXIM_URL,
                params={
                    "authkey": EXIM_API_KEY,
                    "searchdate": rate_date.strftime("%Y%m%d"),
                    "data": "AP01",
                },
                timeout=(5, 10),
            )
            resp.raise_for_status()
            rows = resp.json()
            error_type = None
            break
        except requests.RequestException as exc:
            error_type = type(exc).__name__
            if attempt == _RETRY_ATTEMPTS - 1 or not _is_retryable(exc):
                break
            time.sleep(_backoff_seconds(attempt) + random.uniform(0, _JITTER_MAX_SECONDS))
    if error_type is not None:
        # 메시지에 URL(=authkey 쿼리스트링)이 담기지 않게 한다. try/except 밖에서
        # raise 해야 __context__ 에도 원본 예외(=키 포함 URL)가 안 남는다. `from None`
        # 은 __cause__ 만 끊고 __context__ 는 여전히 채우므로 이것만으론 부족하다.
        raise SourceError(f"수출입은행 호출 실패: {error_type}")

    if not rows:
        # 빈 배열은 '고시 없음'일 수도, 인증 오류일 수도 있다. 둘을 여기서 구분할 수
        # 없으므로 빈 리스트를 돌려주고, 영업일 여부 판정은 alerts 가 한다. (스펙 §1-2 함정②)
        return []

    for row in rows:
        code = row.get("result")
        if code == 4:
            raise RateLimitError(f"수출입은행 result=4 ({_RESULT_MEANING[4]})")
        if code != 1:
            raise SourceError(f"수출입은행 result={code} ({_RESULT_MEANING.get(code, '알 수 없음')})")

    points: list[FxRow] = []
    for cur_unit, (our_code, divisor) in CURRENCIES.items():
        matched = next((r for r in rows if r.get("cur_unit") == cur_unit), None)
        if matched is None:
            raise SourceError(f"응답에 {cur_unit} 가 없다")

        # float() 을 쓰면 금액 정밀도가 깨진다. 콤마를 지우고 Decimal 로 만든다.
        rate = Decimal(matched["deal_bas_r"].replace(",", ""))
        if divisor != 1:
            # JPY(100) 는 100엔당 값이라 나눠서 1엔당으로 정규화한다. Decimal 로
            # 나눠야 한다 — float 을 거치면 895.51/100 같은 정확한 나눗셈도
            # 부동소수 오차가 섞인다. (스펙 §부록 A)
            rate = rate / Decimal(divisor)
        points.append((our_code, rate_date, rate, "koreaexim"))
    return points
