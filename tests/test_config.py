# tests/test_config.py
from collector.config import FX_TABLE, INDEX_TABLE, PROVISIONAL, TICKERS


def test_tickers_and_provisional_cover_same_codes():
    assert set(TICKERS) == set(PROVISIONAL) == {"SPX", "KOSPI"}


def test_kospi_is_provisional_but_spx_is_not():
    assert PROVISIONAL["KOSPI"] is True
    assert PROVISIONAL["SPX"] is False


def test_table_names_keep_test_suffix():
    assert FX_TABLE.endswith("_test")
    assert INDEX_TABLE.endswith("_test")


def test_db_connection_reaches_both_tables(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {FX_TABLE}")
        cur.fetchone()
        cur.execute(f"SELECT count(*) FROM {INDEX_TABLE}")
        cur.fetchone()
