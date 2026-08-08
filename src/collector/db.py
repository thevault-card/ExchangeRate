"""DB 접속과 적재.

적재 규칙의 핵심은 멱등성이다. 값이 그대로면 UPDATE 를 건너뛴다 -> updated_at 이
무의미하게 갱신되지 않아 "실제로 값이 바뀐 날"을 나중에 추적할 수 있다.

지수는 코스피가 yfinance 잠정치라 나중에 공공데이터포털 확정값으로 교체할 예정인데,
그 구분은 `source` 컬럼으로 한다. 대상 테이블(vaultdb silver.market_indices)에
is_provisional 컬럼이 없어 별도 표시를 두지 않기로 했다(2026-08-08).
"""
from datetime import date

import psycopg

from .config import DATABASE_URL, FX_TABLE, INDEX_TABLE
from .sources import FxRow, IndexRow

_UPSERT_INDEX = f"""
INSERT INTO {INDEX_TABLE}
       (index_code, trade_date, close_value, source,
        created_at, updated_at, created_batch_id, updated_batch_id)
VALUES (%s, %s, %s, %s, now(), now(), %s, %s)
ON CONFLICT (index_code, trade_date) DO UPDATE
   SET close_value      = EXCLUDED.close_value,
       source           = EXCLUDED.source,
       updated_at       = now(),
       updated_batch_id = EXCLUDED.updated_batch_id
 WHERE {INDEX_TABLE}.close_value IS DISTINCT FROM EXCLUDED.close_value
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


def upsert_index(conn, points: list[IndexRow], *, batch_id: str) -> int:
    """실제로 쓰인 행 수를 돌려준다. 값이 그대로면 0이 나오고, 그것이 정상이다."""
    written = 0
    with conn.cursor() as cur:
        for code, trade_date, close_value, source in points:
            cur.execute(_UPSERT_INDEX, (code, trade_date, close_value, source,
                                        batch_id, batch_id))
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
