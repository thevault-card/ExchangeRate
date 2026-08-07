# src/collector/config.py
"""환경변수와 상수. 값이 바뀌는 것은 환경변수로, 안 바뀌는 것은 여기 상수로."""
import os
from datetime import timedelta, timezone

# 배치 시각·조회 기준일은 전부 KST 다. 서버 타임존이 UTC 여도 날짜가 어긋나면 안 된다.
KST = timezone(timedelta(hours=9))

DATABASE_URL = os.environ["DATABASE_URL"]
EXIM_API_KEY = os.environ.get("EXIM_API_KEY", "")

# EXIM_API_KEY 발급 전에 스케줄러에 fx_daily 를 넣으면 매일 실패 알림이 나서 진짜
# 장애가 소음에 묻힌다. 기본은 비활성. 키 발급 후 .env 에서 true 로 바꾼다.
FX_ENABLED = os.environ.get("FX_ENABLED", "false").strip().lower() in ("true", "1")

# 검증 단계라 _test 접미사를 유지한다. 실운영 전환 시 이 두 줄만 바꾼다.
FX_TABLE = "silver.fx_exchange_rates_test"
INDEX_TABLE = "silver.market_indices_test"

# 우리 코드 안의 지수 코드 -> yfinance 티커
TICKERS = {"SPX": "^GSPC", "KOSPI": "^KS11"}

# 코스피는 yfinance 값이 공식 확정값이 아니라 나중에 덮어쓸 예정이다. (설계 §4-2)
PROVISIONAL = {"SPX": False, "KOSPI": True}

# 우리 코드의 시장 코드 -> exchange_calendars 캘린더 코드
# 환율(수출입은행)은 은행 영업일이 한국거래소 영업일과 사실상 같으므로 XKRX 로 근사한다.
CALENDARS = {"SPX": "XNYS", "KOSPI": "XKRX", "FX": "XKRX"}

# 세션 마감 후 소스에 데이터가 뜨기까지 주는 유예. 이 시간이 지나야
# "그 세션은 당연히 있어야 한다" 고 판정한다.
# 코스피 15:40 배치는 마감(15:30) 직후라 유예가 없으면 매번 거짓 실패한다.
# 정확한 값은 5거래일 실측 후 조정한다(설계 §13 B-3).
AVAILABILITY_GRACE = {
    "SPX": timedelta(minutes=30),
    "KOSPI": timedelta(minutes=30),
    "FX": timedelta(minutes=30),
}
