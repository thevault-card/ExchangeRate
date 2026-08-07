# src/collector/config.py
"""환경변수와 상수. 값이 바뀌는 것은 환경변수로, 안 바뀌는 것은 여기 상수로."""
import os

DATABASE_URL = os.environ["DATABASE_URL"]
EXIM_API_KEY = os.environ.get("EXIM_API_KEY", "")

# 검증 단계라 _test 접미사를 유지한다. 실운영 전환 시 이 두 줄만 바꾼다.
FX_TABLE = "silver.fx_exchange_rates_test"
INDEX_TABLE = "silver.market_indices_test"

# 우리 코드 안의 지수 코드 -> yfinance 티커
TICKERS = {"SPX": "^GSPC", "KOSPI": "^KS11"}

# 코스피는 yfinance 값이 공식 확정값이 아니라 나중에 덮어쓸 예정이다. (설계 §4-2)
PROVISIONAL = {"SPX": False, "KOSPI": True}
