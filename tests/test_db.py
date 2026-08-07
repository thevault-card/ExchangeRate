from datetime import date
from decimal import Decimal

from collector import db
from collector.config import INDEX_TABLE

D1 = date(2026, 8, 3)


def _rows(conn):
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT close_value, source, is_provisional, created_at, updated_at "
            f"FROM {INDEX_TABLE} WHERE index_code='TEST' AND trade_date=%s",
            (D1,),
        )
        return cur.fetchone()


def test_insert_then_same_value_again_does_not_update(conn):
    p = [("TEST", D1, Decimal("100.00"), "yfinance")]
    assert db.upsert_index(conn, p, provisional=False, batch_id="b1") == 1
    first = _rows(conn)

    assert db.upsert_index(conn, p, provisional=False, batch_id="b2") == 0
    second = _rows(conn)

    assert first[4] == second[4]  # updated_at 이 그대로여야 한다


def test_changed_value_updates(conn):
    db.upsert_index(conn, [("TEST", D1, Decimal("100.00"), "yfinance")],
                    provisional=False, batch_id="b1")
    assert db.upsert_index(conn, [("TEST", D1, Decimal("101.00"), "yfinance")],
                           provisional=False, batch_id="b2") == 1
    assert _rows(conn)[0] == Decimal("101.00")


def test_provisional_must_not_overwrite_final(conn):
    """확정값이 들어간 뒤 잠정 배치가 늦게 돌아도 확정값을 되돌리면 안 된다."""
    db.upsert_index(conn, [("TEST", D1, Decimal("100.00"), "data_go_kr")],
                    provisional=False, batch_id="final")

    written = db.upsert_index(conn, [("TEST", D1, Decimal("99.00"), "yfinance")],
                              provisional=True, batch_id="prov")

    assert written == 0
    row = _rows(conn)
    assert row[0] == Decimal("100.00")
    assert row[2] is False


def test_final_overwrites_provisional_even_when_value_is_same(conn):
    db.upsert_index(conn, [("TEST", D1, Decimal("100.00"), "yfinance")],
                    provisional=True, batch_id="prov")
    assert _rows(conn)[2] is True

    assert db.upsert_index(conn, [("TEST", D1, Decimal("100.00"), "data_go_kr")],
                           provisional=False, batch_id="final") == 1
    row = _rows(conn)
    assert row[2] is False
    assert row[1] == "data_go_kr"


def test_provisional_updates_another_provisional(conn):
    db.upsert_index(conn, [("TEST", D1, Decimal("100.00"), "yfinance")],
                    provisional=True, batch_id="p1")
    assert db.upsert_index(conn, [("TEST", D1, Decimal("102.00"), "yfinance")],
                           provisional=True, batch_id="p2") == 1
    assert _rows(conn)[0] == Decimal("102.00")


def test_latest_date_returns_none_when_empty(conn):
    assert db.latest_date(conn, INDEX_TABLE, "index_code", "NOPE", "trade_date") is None


def test_latest_date_returns_max(conn):
    db.upsert_index(conn, [("TEST", D1, Decimal("100.00"), "yfinance"),
                           ("TEST", date(2026, 8, 4), Decimal("101.00"), "yfinance")],
                    provisional=False, batch_id="b1")
    assert db.latest_date(conn, INDEX_TABLE, "index_code", "TEST", "trade_date") == date(2026, 8, 4)
