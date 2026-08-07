# tests/test_gold_view.py
"""gold.v_market_index_daily 뷰 — carry-forward 검증 (설계 §7, §11).

실데이터(SPX/KOSPI)가 섞이면 판정이 흐려지므로 index_code 를 TESTA/TESTB
전용값으로 쓰고 WHERE 절로 그 코드만 조회한다. conn 픽스처가 롤백하므로
실데이터는 그대로 남는다.
"""
from datetime import date, timedelta
from decimal import Decimal

from collector import db

GOLD_VIEW = "gold.v_market_index_daily"

D1 = date(2026, 8, 1)         # TESTA 의 거래일
D2 = D1 + timedelta(days=1)   # TESTA 는 휴장, TESTB 의 거래일 (서로 어긋난 휴일)


def _row(conn, index_code, calendar_date):
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT close_value, source_trade_date, is_carried_forward "
            f"FROM {GOLD_VIEW} WHERE index_code=%s AND calendar_date=%s",
            (index_code, calendar_date),
        )
        return cur.fetchone()


def test_holiday_carries_forward_previous_value(conn):
    db.upsert_index(conn, [("TESTA", D1, Decimal("100.00"), "test")],
                    provisional=False, batch_id="t1")

    on_session = _row(conn, "TESTA", D1)
    assert on_session == (Decimal("100.00"), D1, False)

    on_holiday = _row(conn, "TESTA", D2)
    assert on_holiday is not None
    close_value, source_trade_date, is_carried_forward = on_holiday
    assert close_value == Decimal("100.00")
    assert source_trade_date == D1
    assert is_carried_forward is True


def test_mismatched_holidays_both_indices_still_filled(conn):
    """TESTA 는 D1 만, TESTB 는 D2 만 거래일이다(서로 다른 휴일 패턴).
    D2 시점에 조회하면 TESTB 는 그날 값, TESTA 는 D1 값을 이어받아 둘 다
    채워져야 한다 — 어느 한쪽도 NULL 이면 안 된다."""
    db.upsert_index(conn, [("TESTA", D1, Decimal("100.00"), "test")],
                    provisional=False, batch_id="t1")
    db.upsert_index(conn, [("TESTB", D2, Decimal("200.00"), "test")],
                    provisional=False, batch_id="t2")

    testa = _row(conn, "TESTA", D2)
    testb = _row(conn, "TESTB", D2)

    assert testa is not None
    assert testb is not None
    assert testa[0] == Decimal("100.00")  # D1 값을 이어받음
    assert testa[2] is True               # carry-forward
    assert testb[0] == Decimal("200.00")  # 그날 자체 값
    assert testb[2] is False              # 실거래일
