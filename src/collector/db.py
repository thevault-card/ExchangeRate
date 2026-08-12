"""DB 접속과 적재.

적재 규칙의 핵심은 멱등성이다. 값이 그대로면 UPDATE 를 건너뛴다 -> updated_at 이
무의미하게 갱신되지 않아 "실제로 값이 바뀐 날"을 나중에 추적할 수 있다.

"값이 그대로"의 판정에는 source 도 포함한다. 코스피 잠정치(yfinance)를 나중에
공공데이터포털 확정값으로 교체할 때 종가가 우연히 같으면, 값만 비교해서는 source 가
'yfinance' 로 남아 잠정/확정 구분이 영영 안 된다. is_provisional 컬럼을 없애고
source 를 유일한 구분 근거로 삼기로 했으므로(2026-08-08) 이 비교는 필수다.

다만 그 비교에는 **방향이 있어야 한다.** 코스피는 yfinance 잠정치라 나중에 공공데이터
포털 확정값으로 교체할 예정인데, 방향이 없으면 확정값이 들어간 뒤 다음 yfinance 배치가
같은 날짜를 다시 받아 확정값을 잠정치로 되돌린다. 설계 §6 의 "잠정은 확정을 못 건드리고,
확정은 항상 이긴다" 가 is_provisional 컬럼 삭제와 함께 사라졌던 자리다. 잠정 소스는
yfinance 하나뿐이므로 아래 SQL 의 CASE 한 줄이 그 규칙 전부다.
"""
from datetime import date

import psycopg

from .config import DATABASE_URL, FX_TABLE, INDEX_TABLE
from .sources import FxRow, IndexRow

# 잠정치를 주는 소스. 이 소스는 다른 소스가 쓴 행을 건드리지 못한다.
PROVISIONAL_SOURCE = "yfinance"

def _rank(col: str) -> str:
    """0 = 잠정, 1 = 확정. 확정끼리는 나중 것이 이긴다(정정 고시를 반영해야 하므로)."""
    return f"CASE WHEN {col} = '{PROVISIONAL_SOURCE}' THEN 0 ELSE 1 END"


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
 WHERE ({INDEX_TABLE}.close_value IS DISTINCT FROM EXCLUDED.close_value
        OR {INDEX_TABLE}.source   IS DISTINCT FROM EXCLUDED.source)
   AND {_rank('EXCLUDED.source')} >= {_rank(f'{INDEX_TABLE}.source')}
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
    OR {FX_TABLE}.source    IS DISTINCT FROM EXCLUDED.source
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
