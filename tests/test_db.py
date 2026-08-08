from datetime import date
from decimal import Decimal

import psycopg
import pytest

from collector import db
from collector.config import INDEX_TABLE

D1 = date(2026, 8, 3)


def _rows(conn):
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT close_value, source, created_at, updated_at, "
            f"updated_batch_id "
            f"FROM {INDEX_TABLE} WHERE index_code='TEST' AND trade_date=%s",
            (D1,),
        )
        return cur.fetchone()


def test_insert_then_same_value_again_does_not_update(conn):
    p = [("TEST", D1, Decimal("100.00"), "yfinance")]
    assert db.upsert_index(conn, p, batch_id="b1") == 1
    first = _rows(conn)

    assert db.upsert_index(conn, p, batch_id="b2") == 0
    second = _rows(conn)

    # updated_at 은 트랜잭션 안에서 now() 가 상수라 항상 같게 나와 이 assert 로는
    # UPDATE 가 건너뛰어졌는지 검증되지 않는다. 두 번째 호출은 batch_id="b2" 로 들어
    # 갔으므로, UPDATE 가 정말 건너뛰어졌다면 updated_batch_id 는 여전히 "b1" 이어야
    # 한다.
    assert second[4] == "b1"
    assert first[3] == second[3]  # updated_at 도 그대로 (참고용, 위 assert 가 본검증)


def test_changed_value_updates(conn):
    db.upsert_index(conn, [("TEST", D1, Decimal("100.00"), "yfinance")], batch_id="b1")
    assert db.upsert_index(conn, [("TEST", D1, Decimal("101.00"), "yfinance")], batch_id="b2") == 1
    assert _rows(conn)[0] == Decimal("101.00")





def test_latest_date_returns_none_when_empty(conn):
    assert db.latest_date(conn, INDEX_TABLE, "index_code", "NOPE", "trade_date") is None


def test_nan_close_value_is_rejected_by_db(conn):
    """NaN 은 PostgreSQL 에서 0보다 '크다'고 판정돼 CHECK (close_value > 0) 만으로는
    안 걸러진다. `< 'Infinity'` 를 더한 제약이 실제로 막는지 확인한다."""
    p = [("TEST", D1, Decimal("NaN"), "yfinance")]
    with pytest.raises(psycopg.Error):
        db.upsert_index(conn, p, batch_id="b1")


def test_latest_date_returns_max(conn):
    db.upsert_index(conn, [("TEST", D1, Decimal("100.00"), "yfinance"),
                           ("TEST", date(2026, 8, 4), Decimal("101.00"), "yfinance")], batch_id="b1")
    assert db.latest_date(conn, INDEX_TABLE, "index_code", "TEST", "trade_date") == date(2026, 8, 4)
