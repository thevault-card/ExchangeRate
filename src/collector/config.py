# src/collector/config.py
"""환경변수와 상수. 값이 바뀌는 것은 환경변수로, 안 바뀌는 것은 여기 상수로."""
import os
from datetime import date, timedelta, timezone

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

# 수출입은행 응답의 cur_unit -> (우리 통화코드, 나누는 단위)
# JPY 는 cur_unit 이 'JPY(100)' 이고 값이 100엔당이라 100 으로 나눠 1엔당으로 만든다.
# (스펙 §부록 A) 나누는 단위를 빠뜨리면 환율이 100배로 잘못 저장된다.
CURRENCIES: dict[str, tuple[str, int]] = {
    "USD": ("USD", 1),
    "JPY(100)": ("JPY", 100),
}

# 통화별 신선도·이상치 판정, (통화, 날짜) 백필 판정에 쓰는 우리 통화코드 목록.
FX_CURRENCY_CODES = sorted({code for code, _ in CURRENCIES.values()})

# 우리 코드 안의 지수 코드 -> yfinance 티커
TICKERS = {"SPX": "^GSPC", "KOSPI": "^KS11"}

# 코스피는 yfinance 값이 공식 확정값이 아니라 나중에 덮어쓸 예정이다. (설계 §4-2)
PROVISIONAL = {"SPX": False, "KOSPI": True}

# 우리 코드의 시장 코드 -> exchange_calendars 캘린더 코드
# 환율(수출입은행)은 은행 영업일이 한국거래소 영업일과 사실상 같으므로 XKRX 로 근사한다.
# 근사가 어긋나는 곳: KRX 는 연말 폐장일(12/29·12/31)에 닫지만 은행은 영업해 고시가
# 나온다. 방향이 "세션 아닌데 데이터 있음" 이라 거짓 실패를 만들지 않아 무해하다.
CALENDARS = {"SPX": "XNYS", "KOSPI": "XKRX", "FX": "XKRX"}

# exchange_calendars 가 모르는 휴장일. 캘린더 코드별 집합.
#
# 3년치(2023-08 ~ 2026-08) 실측 대조 결과 XNYS 는 751/751 로 완전 일치했고,
# XKRX 는 아래 2일이 틀렸다. 캘린더가 "개장" 이라는데 실제로는 코스피 종가도
# 환율 고시도 없어서, 그대로 두면 그 날 배치가 거짓 실패한다.
#
# 제헌절은 공휴일로 복원돼 **매년 반복**된다. 새 연도가 시작되면 7/17 을 추가할 것.
# 임시공휴일(선거일 등)은 발생할 때마다 추가한다 — 거짓 실패 알림이 오면 그게 신호다.
EXTRA_CLOSURES = {
    "XKRX": frozenset({
        date(2026, 6, 3),   # 제9회 전국동시지방선거
        date(2026, 7, 17),  # 제헌절 (공휴일 복원)
    }),
}

# 세션 마감 후 소스에 데이터가 뜨기까지 주는 유예. 이 시간이 지나야
# "그 세션은 당연히 있어야 한다" 고 판정한다.
# 코스피 15:40 배치는 마감(15:30) 직후라 유예가 없으면 매번 거짓 실패한다.
# 정확한 값은 5거래일 실측 후 조정한다(설계 §13 B-3).
AVAILABILITY_GRACE = {
    "SPX": timedelta(minutes=30),
    "KOSPI": timedelta(minutes=30),
    "FX": timedelta(minutes=30),
}
