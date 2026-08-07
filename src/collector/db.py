"""DB 접속과 적재.

적재 규칙의 핵심은 '덮어쓰기 방향'이다 (설계 §6).
  - 잠정값은 확정값을 절대 못 건드린다.
  - 확정값은 잠정값을 항상 이긴다.
  - 값이 그대로면 UPDATE를 건너뛴다 -> updated_at 이 무의미하게 갱신되지 않는다.
"""
from datetime import date
from decimal import Decimal

import psycopg

from .config import DATABASE_URL, FX_TABLE, INDEX_TABLE

IndexRow = tuple[str, date, Decimal, str]  # index_code, trade_date, close_value, source
FxRow = tuple[str, date, Decimal, str]     # currency_code, rate_date, base_rate, source

_INDEX_COLS = """(index_code, trade_date, close_value, source, is_provisional,
                  created_at, updated_at, created_batch_id, updated_batch_id)"""

# 잠정 적재: is_provisional = true 인 행만 건드린다. 이 조건이 확정본을 지키는 방어선이다.
_UPSERT_INDEX_PROVISIONAL = f"""
INSERT INTO {INDEX_TABLE} {_INDEX_COLS}
VALUES (%s, %s, %s, %s, true, now(), now(), %s, %s)
ON CONFLICT (index_code, trade_date) DO UPDATE
   SET close_value      = EXCLUDED.close_value,
       source           = EXCLUDED.source,
       updated_at       = now(),
       updated_batch_id = EXCLUDED.updated_batch_id
 WHERE {INDEX_TABLE}.is_provisional = true
   AND {INDEX_TABLE}.close_value IS DISTINCT FROM EXCLUDED.close_value
"""

# 확정 적재: 잠정본이면 값이 같아도 덮어써서 is_provisional 을 false 로 내린다.
_UPSERT_INDEX_FINAL = f"""
INSERT INTO {INDEX_TABLE} {_INDEX_COLS}
VALUES (%s, %s, %s, %s, false, now(), now(), %s, %s)
ON CONFLICT (index_code, trade_date) DO UPDATE
   SET close_value      = EXCLUDED.close_value,
       source           = EXCLUDED.source,
       is_provisional   = false,
       updated_at       = now(),
       updated_batch_id = EXCLUDED.updated_batch_id
 WHERE {INDEX_TABLE}.is_provisional = true
    OR {INDEX_TABLE}.close_value IS DISTINCT FROM EXCLUDED.close_value
"""

_UPSERT_FX = f"""
INSERT INTO {FX_TABLE}
       (currency_code, rate_date, base_rate, source,
        created_at, updated_at, created_batch_id, updated_batch_id)
VALUES (%s, %s, %s, %s, now(), now(), %s, %s)
ON CONFLICT (currency_code, rate_date) DO UPDATE
   SET base_rate        = EXCLUDED.base_rate,
       source           = EXCLUDED.source,
       updated_at       = now(),
       updated_batch_id = EXCLUDED.updated_batch_id
 WHERE {FX_TABLE}.base_rate IS DISTINCT FROM EXCLUDED.base_rate
"""


def connect() -> psycopg.Connection:
    return psycopg.connect(DATABASE_URL)


def upsert_index(conn, points: list[IndexRow], *, provisional: bool, batch_id: str) -> int:
    """실제로 쓰인 행 수를 돌려준다. 값이 그대로면 0이 나오고, 그것이 정상이다."""
    sql = _UPSERT_INDEX_PROVISIONAL if provisional else _UPSERT_INDEX_FINAL
    written = 0
    with conn.cursor() as cur:
        for code, trade_date, close_value, source in points:
            cur.execute(sql, (code, trade_date, close_value, source, batch_id, batch_id))
            written += cur.rowcount
    return written


def upsert_fx(conn, point: FxRow, *, batch_id: str) -> int:
    code, rate_date, base_rate, source = point
    with conn.cursor() as cur:
        cur.execute(_UPSERT_FX, (code, rate_date, base_rate, source, batch_id, batch_id))
        return cur.rowcount


def latest_date(conn, table: str, key_col: str, key: str, date_col: str) -> date | None:
    with conn.cursor() as cur:
        cur.execute(f"SELECT max({date_col}) FROM {table} WHERE {key_col} = %s", (key,))
        return cur.fetchone()[0]
