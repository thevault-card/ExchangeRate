# tests/test_config.py
import importlib

from collector import config as config_module
from collector.config import (
    CURRENCIES,
    FX_CURRENCY_CODES,
    FX_TABLE,
    INDEX_TABLE,
    PROVISIONAL,
    TICKERS,
)


def test_tickers_and_provisional_cover_same_codes():
    assert set(TICKERS) == set(PROVISIONAL) == {"SPX", "KOSPI"}


def test_currencies_maps_jpy_100_unit_with_divisor_100():
    """JPY(100) 을 빠뜨리거나 나누는 단위를 잘못 적으면 환율이 100배로 저장된다."""
    assert CURRENCIES["JPY(100)"] == ("JPY", 100)
    assert CURRENCIES["USD"] == ("USD", 1)
    assert FX_CURRENCY_CODES == ["JPY", "USD"]


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


def test_fx_enabled_defaults_to_false_and_parses_true_strings(monkeypatch):
    """EXIM_API_KEY 발급 전 사고로 켜지지 않게, 기본값은 비활성이어야 한다."""
    try:
        monkeypatch.delenv("FX_ENABLED", raising=False)
        importlib.reload(config_module)
        assert config_module.FX_ENABLED is False

        for value in ("true", "True", "TRUE", "1"):
            monkeypatch.setenv("FX_ENABLED", value)
            importlib.reload(config_module)
            assert config_module.FX_ENABLED is True

        for value in ("false", "0", ""):
            monkeypatch.setenv("FX_ENABLED", value)
            importlib.reload(config_module)
            assert config_module.FX_ENABLED is False
    finally:
        monkeypatch.delenv("FX_ENABLED", raising=False)
        importlib.reload(config_module)
