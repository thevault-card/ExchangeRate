"""실패·경고 판정 (설계 §9-1).

'조용히 안 쌓이는 것'이 가장 위험하다. 판정은 재량이 아니라 필수 요구사항이다.
"""
from datetime import date, timedelta
from decimal import Decimal


class BatchFailure(RuntimeError):
    """배치를 실패로 끝내야 하는 상황. 호출자는 종료코드 1로 나간다."""


def is_business_day(day: date) -> bool:
    # ponytail: 요일만 본다. 공휴일에는 오탐(경고)이 난다.
    # 오탐이 거슬리면 holidays 패키지를 붙인다.
    return day.weekday() < 5


def check_not_empty(rows, *, on: date, label: str) -> None:
    """영업일인데 한 건도 못 받았으면 실패. 주말은 정상이다."""
    if rows:
        return
    if is_business_day(on):
        raise BatchFailure(f"{label}: 영업일({on})인데 0건")


def check_staleness(last_loaded: date | None, *, today: date, max_business_days: int = 3) -> None:
    """마지막 적재일이 너무 오래됐으면 실패. 한 번도 안 쌓였으면 판정하지 않는다."""
    if last_loaded is None:
        return
    elapsed = sum(
        1
        for offset in range(1, (today - last_loaded).days + 1)
        if is_business_day(last_loaded + timedelta(days=offset))
    )
    if elapsed >= max_business_days:
        raise BatchFailure(f"마지막 적재일 {last_loaded} 이후 영업일 {elapsed}일 경과")


def check_outlier(previous: Decimal | None, current: Decimal, *, threshold: Decimal) -> str | None:
    """전일 대비 변동이 임계값을 넘으면 경고 문구를 돌려준다. 파싱 버그 탐지용."""
    if previous is None or previous == 0:
        return None
    change = abs(current - previous) / previous
    if change <= threshold:
        return None
    return f"전일 대비 {change * 100:.1f}% 변동 ({previous} -> {current})"
