"""외부에서 값을 가져온다. 여기가 소스 교체 지점이다.

yfinance 는 Yahoo Finance 의 비공식 엔드포인트를 쓰는 라이브러리다. 상용 서비스
전환 시 유료 API(S&P500)와 공공데이터포털(코스피)로 교체하는 것을 전제로 한다.
교체할 때 fetch_index 의 본문만 바뀌고 호출하는 쪽은 그대로다. (설계 §8)
"""
import math
from datetime import date, datetime, timedelta
from decimal import Decimal

import requests
import yfinance as yf

from .config import EXIM_API_KEY, KST, TICKERS

IndexRow = tuple[str, date, Decimal, str]


class SourceError(RuntimeError):
    """외부 소스가 실패로 응답했을 때. 재시도해도 소용없는 상황을 포함한다."""


def fetch_index(index_code: str, lookback_days: int = 5) -> list[IndexRow]:
    """오늘로부터 lookback_days 일 전까지의 일별 종가.

    period= 대신 start= 를 쓴다. yfinance 의 period 는 '5d','1y','max' 같은 정해진
    값만 받아서 '1095d' 를 넘기면 동작하지 않는다. start= 는 임의 기간이 되므로
    평소 수집(5일)과 초기 백필(3년)이 같은 코드로 처리된다.
    """
    ticker = TICKERS[index_code]
    start = datetime.now(KST).date() - timedelta(days=lookback_days)
    df = yf.download(ticker, start=start, interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        return []

    closes = df["Close"]
    if hasattr(closes, "columns"):  # MultiIndex 컬럼이면 첫 열이 우리 티커다
        closes = closes.iloc[:, 0]

    points: list[IndexRow] = []
    for stamp, value in closes.items():
        if value is None:
            continue
        number = float(value)
        if math.isnan(number):
            # 휴장일이나 장중 미확정 구간. 적재하면 확정 종가를 덮어쓴다.
            continue
        points.append((index_code, stamp.date(), Decimal(f"{number:.2f}"), "yfinance"))
    return points


FxRow = tuple[str, date, Decimal, str]

# 구 도메인(www.koreaexim.go.kr)은 2026-04-30 서비스 종료. 인터넷 예제 코드 대부분이
# 구 도메인이라 그대로 복붙하면 동작하지 않는다. (스펙 §1-1)
_EXIM_URL = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON"

_RESULT_MEANING = {
    2: "데이터코드 오류",
    3: "인증코드 오류 (키 만료 신호)",
    4: "일일제한 초과",
}


def fetch_fx(rate_date: date) -> FxRow | None:
    """USD 매매기준율 1건. 고시가 없는 날(주말·공휴일)이면 None."""
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

    if not rows:
        # 빈 배열은 '고시 없음'일 수도, 인증 오류일 수도 있다. 둘을 여기서 구분할 수
        # 없으므로 None 을 돌려주고, 영업일 여부 판정은 alerts 가 한다. (스펙 §1-2 함정②)
        return None

    for row in rows:
        code = row.get("result")
        if code != 1:
            raise SourceError(f"수출입은행 result={code} ({_RESULT_MEANING.get(code, '알 수 없음')})")

    usd = next((r for r in rows if r.get("cur_unit") == "USD"), None)
    if usd is None:
        raise SourceError("응답에 USD 가 없다")

    # float() 을 쓰면 금액 정밀도가 깨진다. 콤마를 지우고 Decimal 로 만든다.
    base_rate = Decimal(usd["deal_bas_r"].replace(",", ""))
    return ("USD", rate_date, base_rate, "koreaexim")
