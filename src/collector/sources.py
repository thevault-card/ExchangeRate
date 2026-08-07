"""외부에서 값을 가져온다. 여기가 소스 교체 지점이다.

yfinance 는 Yahoo Finance 의 비공식 엔드포인트를 쓰는 라이브러리다. 상용 서비스
전환 시 유료 API(S&P500)와 공공데이터포털(코스피)로 교체하는 것을 전제로 한다.
교체할 때 fetch_index 의 본문만 바뀌고 호출하는 쪽은 그대로다. (설계 §8)
"""
import math
from datetime import date, timedelta
from decimal import Decimal

import yfinance as yf

from .config import TICKERS

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
    start = date.today() - timedelta(days=lookback_days)
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
