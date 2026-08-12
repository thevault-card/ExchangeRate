"""외부에서 값을 가져온다. 여기가 소스 교체 지점이다.

yfinance 는 Yahoo Finance 의 비공식 엔드포인트를 쓰는 라이브러리다. 상용 서비스
전환 시 유료 API(S&P500)와 공공데이터포털(코스피)로 교체하는 것을 전제로 한다.
교체할 때 fetch_index 의 본문만 바뀌고 호출하는 쪽은 그대로다. (설계 §8)
"""
import random
import time
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import pandas as pd
import requests
import yfinance as yf

from .config import CURRENCIES, EXIM_API_KEY, KST, TICKERS
from .logs import log

IndexRow = tuple[str, date, Decimal, str]


class SourceError(RuntimeError):
    """외부 소스가 실패로 응답했을 때. 재시도해도 소용없는 상황을 포함한다."""


def _checked(value: Decimal, *, what: str) -> Decimal:
    """적재해도 되는 금액인지 확인한다. 아니면 배치 전체를 실패시킨다.

    0·음수·Infinity·NaN 은 파싱이 깨졌다는 신호다. 로컬 DB 는 CHECK 제약이 막아주지만
    적재 대상인 vaultdb 에는 그 제약이 없어(2026-08-11 확인) 그대로 들어간다. 게다가
    UPSERT 라 **이미 들어가 있던 정상값을 덮어쓴다.** 한 건만 조용히 건너뛰지 않고
    배치를 세우는 이유는, 이런 값이 나왔다면 다른 행도 믿을 수 없기 때문이다.
    """
    if not value.is_finite() or value <= 0:
        raise SourceError(f"{what}: 적재할 수 없는 값 {value}")
    return value


class RateLimitError(SourceError):
    """수출입은행 일일 호출 한도 초과(result=4). 백필이 "오늘은 여기까지"를
    "진짜 실패"와 구분할 수 있어야 해서 SourceError 와 별도로 잡을 수 있게 한다."""


# 재시도 정책 (설계 §9-3, 스펙 §4-3). 두 소스가 같은 뼈대를 쓰고 백오프 시작점만 다르다.
# 재시도 대상은 타임아웃·커넥션 오류·429·5xx 뿐이다. 그 외(다른 4xx, result 코드 오류,
# USD 없음)는 재시도해도 결과가 같으므로 즉시 실패시킨다.
_RETRY_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1     # 수출입은행: 1 -> 2 -> 4
_YF_BACKOFF_BASE_SECONDS = 2  # yfinance: 2 -> 4 -> 8 (설계 §9-3)
_JITTER_MAX_SECONDS = 0.25


def _backoff_seconds(attempt: int, base: float = _BACKOFF_BASE_SECONDS) -> float:
    """0-based 시도 번호에 대한 백오프 초(jitter 제외). base, 2*base, 4*base 순서다."""
    return base * (2 ** attempt)


def _is_retryable(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return False


def _download_with_retry(ticker: str, start: date):
    """yf.download 를 재시도로 감싼다. 설계 §9-3 이 규정했는데 빠져 있던 부분.

    yfinance 는 비공식 엔드포인트라 예외 종류가 문서화돼 있지 않다. 그래서 타입으로
    거르지 않고 전부 재시도한 뒤, 끝까지 실패하면 SourceError 로 바꾼다 — 잡지 않으면
    traceback 이 그대로 찍혀 stdout JSON 한 줄 규약이 깨진다.
    """
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            return yf.download(ticker, start=start, interval="1d",
                               auto_adjust=False, progress=False)
        except Exception as exc:  # noqa: BLE001 (예외 종류가 문서화돼 있지 않다)
            if attempt == _RETRY_ATTEMPTS - 1:
                raise SourceError(f"yfinance 호출 실패: {type(exc).__name__}") from None
            time.sleep(_backoff_seconds(attempt, _YF_BACKOFF_BASE_SECONDS)
                       + random.uniform(0, _JITTER_MAX_SECONDS))
    return None


def fetch_index(index_code: str, lookback_days: int = 5) -> tuple[list[IndexRow], int]:
    """오늘로부터 lookback_days 일 전까지의 일별 종가 목록과 건너뛴 NaN 건수.

    period= 대신 start= 를 쓴다. yfinance 의 period 는 '5d','1y','max' 같은 정해진
    값만 받아서 '1095d' 를 넘기면 동작하지 않는다. start= 는 임의 기간이 되므로
    평소 수집(5일)과 초기 백필(3년)이 같은 코드로 처리된다.
    """
    ticker = TICKERS[index_code]
    start = datetime.now(KST).date() - timedelta(days=lookback_days)
    df = _download_with_retry(ticker, start)
    if df is None or df.empty:
        return [], 0

    try:
        closes = df["Close"]
        if hasattr(closes, "columns"):  # MultiIndex 컬럼이면 첫 열이 우리 티커다
            closes = closes.iloc[:, 0]
        items = list(closes.items())
    except (KeyError, IndexError, AttributeError) as exc:
        # 응답 모양이 바뀐 경우. traceback 으로 죽으면 stdout JSON 한 줄 규약이 깨져
        # 운영에서 실패 집계가 어긋난다. SourceError 로 바꿔 run() 이 잡게 한다.
        raise SourceError(f"yfinance 응답 형식이 예상과 다름: {type(exc).__name__}") from None

    points: list[IndexRow] = []
    skipped = 0
    for stamp, value in items:
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
        try:
            close_value = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise SourceError(f"{index_code} {stamp}: 종가를 숫자로 못 읽음") from None
        _checked(close_value, what=f"{index_code} {stamp} 종가")
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

    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        # 인증 실패 시 JSON 대신 다른 모양이 오는 사례가 있다. dict 를 가정하고
        # .get 을 부르면 AttributeError 로 죽어 로그 규약이 깨진다.
        raise SourceError("수출입은행 응답 형식이 예상과 다름")

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
        try:
            rate = Decimal(matched["deal_bas_r"].replace(",", ""))
        except (KeyError, AttributeError, TypeError, InvalidOperation):
            raise SourceError(f"{cur_unit}: deal_bas_r 을 숫자로 못 읽음") from None
        if divisor != 1:
            # JPY(100) 는 100엔당 값이라 나눠서 1엔당으로 정규화한다. Decimal 로
            # 나눠야 한다 — float 을 거치면 895.51/100 같은 정확한 나눗셈도
            # 부동소수 오차가 섞인다. (스펙 §부록 A)
            rate = rate / Decimal(divisor)
        _checked(rate, what=f"{our_code} {rate_date} 매매기준율")
        points.append((our_code, rate_date, rate, "koreaexim"))
    return points
