# tests/test_config.py
import importlib
import os

import pytest

from collector import config as config_module
from collector.config import (
    CURRENCIES,
    FX_CURRENCY_CODES,
    FX_TABLE,
    INDEX_TABLE,
    TICKERS,
)


def test_tickers_and_provisional_cover_same_codes():
    assert set(TICKERS) == {"SPX", "KOSPI"}


def test_currencies_maps_jpy_100_unit_with_divisor_100():
    """JPY(100) 을 빠뜨리거나 나누는 단위를 잘못 적으면 환율이 100배로 저장된다."""
    assert CURRENCIES["JPY(100)"] == ("JPY", 100)
    assert CURRENCIES["USD"] == ("USD", 1)
    assert FX_CURRENCY_CODES == ["JPY", "USD"]



def test_table_names_match_target_schema():
    """적재 대상(vaultdb)과 이름이 같아야 한다. 로컬 DB 도 같은 이름으로 맞춰뒀다."""
    assert FX_TABLE == "silver.fx_exchange_rates"
    assert INDEX_TABLE == "silver.market_indices"


def test_db_connection_reaches_both_tables(conn):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {FX_TABLE}")
        cur.fetchone()
        cur.execute(f"SELECT count(*) FROM {INDEX_TABLE}")
        cur.fetchone()


def test_fx_enabled_must_be_explicit_and_parses_true_strings(monkeypatch):
    """기본값을 두지 않는다 — 빠뜨리면 조용히 꺼진 채 도는 것보다 죽는 편이 낫다.

    기본 false 를 두면 새 환경(EC2)에 변수를 빠뜨렸을 때 환율 배치가 매번
    skipped + 종료코드 0 으로 끝나 영구히 안 도는 것을 아무도 모른다.
    """
    original = os.environ["FX_ENABLED"]
    try:
        monkeypatch.delenv("FX_ENABLED", raising=False)
        with pytest.raises(RuntimeError, match="FX_ENABLED"):
            importlib.reload(config_module)

        for value in ("true", "True", "TRUE", "1"):
            monkeypatch.setenv("FX_ENABLED", value)
            importlib.reload(config_module)
            assert config_module.FX_ENABLED is True

        for value in ("false", "0", ""):
            monkeypatch.setenv("FX_ENABLED", value)
            importlib.reload(config_module)
            assert config_module.FX_ENABLED is False
    finally:
        monkeypatch.setenv("FX_ENABLED", original)
        importlib.reload(config_module)
