"""판정 규칙 — 언제 받아야 하고, 무엇이 실패인가. (설계 §9-1)

세 가지를 판단한다.
  is_session / last_due_session  이 거래소의 어느 세션까지 데이터가 있어야 하나
  check_freshness                그 세션이 비어 있으면 배치 실패
  check_outlier                  전일 대비 변동이 과하면 경고(파싱 버그 탐지용)

'조용히 안 쌓이는 것'이 가장 위험하다. 판정은 재량이 아니라 필수 요구사항이다.

영업일 판정은 요일이 아니라 거래소 캘린더로 한다. "오늘이 영업일인가"를 묻지 않고
"이 거래소의 가장 최근 마감 세션까지 우리가 갖고 있는가"를 묻는다 — 공휴일과 토요일
실행(index_spx 는 06:30 KST 실행분이 미국 금요일 세션을 받는다)이 한 규칙으로 처리된다.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal
from functools import cache

import exchange_calendars as xcals

from .config import AVAILABILITY_GRACE, CALENDARS, EXTRA_CLOSURES


class BatchFailure(RuntimeError):
    """배치를 실패로 끝내야 하는 상황. 호출자는 종료코드 1로 나간다."""


@cache
def _calendar(market: str):
    # exchange_calendars 캘린더 객체는 만드는 비용이 크다. 시장당 하나만 만든다.
    return xcals.get_calendar(CALENDARS[market])


def is_session(market: str, day: date) -> bool:
    """그 거래소가 그 날 열었는가.

    캘린더가 모르는 휴장일은 EXTRA_CLOSURES 로 덮는다. 3년치 실측에서 XKRX 가
    2일(제헌절·지방선거) 틀렸다. 그 날을 세션으로 보면 데이터가 없다고 거짓
    실패한다.
    """
    if day in EXTRA_CLOSURES.get(CALENDARS[market], frozenset()):
        return False
    return bool(_calendar(market).is_session(day))


def last_due_session(market: str, now: datetime) -> date | None:
    """마감 시각 + 유예가 이미 지난 세션 중 가장 최근 것.

    이 날짜까지는 데이터가 있어야 정상이다. 아직 아무 세션도 안 지났으면 None.
    연휴가 아무리 길어도 30일을 넘지 않으므로 최근 30일만 훑는다.
    """
    cal = _calendar(market)
    grace = AVAILABILITY_GRACE[market]
    sessions = cal.sessions_in_range(now.date() - timedelta(days=30), now.date())
    due = None
    for session in sessions:
        if not is_session(market, session.date()):
            continue  # 캘린더가 모르는 휴장일 (EXTRA_CLOSURES)
        if cal.session_close(session) + grace <= now:
            due = session.date()
    return due


def check_freshness(market: str, latest_stored: date | None, now: datetime) -> None:
    """마감·유예가 지난 세션까지 적재됐는지. 아니면 BatchFailure."""
    due = last_due_session(market, now)
    if due is None:
        return  # 아직 아무 세션도 마감·유예를 안 지났다. 평가 대상이 없다.
    if latest_stored is None or latest_stored < due:
        raise BatchFailure(
            f"{market}: 마감 세션 {due} 인데 최신 적재일이 {latest_stored}"
        )


def check_outlier(previous: Decimal | None, current: Decimal, *, threshold: Decimal) -> str | None:
    """전일 대비 변동이 임계값을 넘으면 경고 문구를 돌려준다. 파싱 버그 탐지용."""
    if previous is None or previous == 0:
        return None
    change = abs(current - previous) / previous
    if change <= threshold:
        return None
    return f"전일 대비 {change * 100:.1f}% 변동 ({previous} -> {current})"
