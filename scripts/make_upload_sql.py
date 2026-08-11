"""TheVault DB 에 직접 붙여넣을 INSERT SQL 파일을 만든다.

    uv run --env-file .env python scripts/make_upload_sql.py fx    silver.fx_exchange_rate
    uv run --env-file .env python scripts/make_upload_sql.py index silver.market_indices

export/ 에 .sql 파일이 생긴다. DBeaver 에서 열어 실행하면 된다.
이 스크립트는 **로컬 DB 만 읽는다.** TheVault DB 에는 접속하지 않는다.

적재 규칙 (2026-08-07 지시):
  fx     created_at / updated_at 을 원본 값 그대로
  index  created_at / updated_at 을 적재 시점(now())
  둘 다  created_batch_id / updated_batch_id = 'vaultuser' (대상 DB 접속 계정)
  둘 다  ON CONFLICT DO NOTHING — 이미 있는 행은 건드리지 않는다
"""
import os
import sys
from pathlib import Path

import psycopg

OUT = Path(__file__).resolve().parent.parent / "export"

# batch_id 는 TheVault 파이프라인 관례를 따른다: **대상 DB 접속 계정명**
# (성호님 get_batch_id() 와 같은 값). "어느 실행" 이 아니라 "어느 계정" 이 쓴 행인지를
# 남기는 컬럼이라 실행 시각을 붙이지 않는다. 이 스크립트는 대상 DB 에 접속하지 않아
# 계정을 물어볼 곳이 없으므로 하드코딩한다 — 계정이 바뀌면 이 줄을 고친다.
BATCH_ID = "vaultuser"

# TheVault 파이프라인이 created_at/updated_at 을 date_trunc('second', NOW()) 로
# 넣으므로 초 단위로 맞춘다. 마이크로초를 남기면 같은 컬럼에 정밀도가 섞인다.
SECOND = "second"

JOBS = {
    "fx": {
        "select": """SELECT rate_date, currency_code, base_rate, source, created_at, updated_at
                       FROM silver.fx_exchange_rates_test ORDER BY rate_date""",
        "cols": "rate_date, currency_code, base_rate, source, "
                "created_at, updated_at, created_batch_id, updated_batch_id",
        "row": lambda r: (f"('{r[0]}', '{r[1]}', {r[2]}, '{r[3]}', "
                          f"'{r[4].replace(microsecond=0).isoformat()}', "
                          f"'{r[5].replace(microsecond=0).isoformat()}', "
                          f"'{BATCH_ID}', '{BATCH_ID}')"),
        "note": "created_at/updated_at 은 수집 당시 원본 값을 초 단위로 잘라 넣는다.",
    },
    # source 는 'yfinance' 를 넣는다(2026-08-08 지시). 코스피가 공식 확정값이 아니라
    # 나중에 공공데이터포털 값으로 교체할 예정인데, 대상 테이블에 별도 표시 컬럼이
    # 없으므로 이 컬럼이 유일한 구분 근거가 된다.
    "index": {
        "select": """SELECT index_code, trade_date, close_value, source
                       FROM silver.market_indices_test ORDER BY index_code, trade_date""",
        "cols": "index_code, trade_date, close_value, source, "
                "created_at, updated_at, created_batch_id, updated_batch_id",
        "row": lambda r: (f"('{r[0]}', '{r[1]}', {r[2]}, '{r[3]}', "
                          f"date_trunc('second', now()), date_trunc('second', now()), "
                          f"'{BATCH_ID}', '{BATCH_ID}')"),
        "note": "created_at/updated_at 은 실행 시점(초 단위), source 는 수집 소스 그대로.",
    },
}

HEADER = """-- {job} -> {table}
-- 만든 날: {stamp}
-- 원본: {src_count}행
--
-- {note}
-- created_batch_id / updated_batch_id = '{batch}'
--
-- ON CONFLICT DO NOTHING 이므로 이미 있는 키는 건너뛴다. 여러 번 돌려도 안전하다.
-- 바꿔 말하면 **이미 있는 행의 값은 갱신되지 않는다.**
--
-- 실행 전에 확인:
--     SELECT currency_code, count(*), min(base_rate), max(base_rate)
--       FROM {table} GROUP BY 1;
--
-- 실행 후 확인:
--     SELECT count(*) FROM {table};

BEGIN;

INSERT INTO {table}
       ({cols})
VALUES
"""

FOOTER = """
ON CONFLICT DO NOTHING;

-- ↑ 여기까지가 INSERT 다. 아직 확정되지 않았다(BEGIN 으로 트랜잭션이 열려 있다).
--
-- 이제 아래를 **직접** 실행해 확인한 뒤 결정한다:
--
--     SELECT count(*) FROM {table};
--     SELECT * FROM {table} ORDER BY 1 DESC LIMIT 10;
--
-- 맞으면    COMMIT;
-- 이상하면  ROLLBACK;      <- 아무것도 안 들어간 상태로 되돌아간다
--
-- COMMIT 을 이 파일에 넣지 않은 이유: 눈으로 보기 전에 확정되면 안 된다.
"""


def main() -> int:
    if len(sys.argv) != 3 or sys.argv[1] not in JOBS:
        raise SystemExit(f"사용법: {sys.argv[0]} [{'|'.join(JOBS)}] <schema.table>")
    job_name, table = sys.argv[1], sys.argv[2]
    job = JOBS[job_name]

    conn = psycopg.connect(os.environ["DATABASE_URL"])
    rows = conn.execute(job["select"]).fetchall()
    stamp = conn.execute("SELECT now()").fetchone()[0].isoformat()

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"upload_{job_name}.sql"
    with path.open("w", encoding="utf-8") as f:
        f.write(HEADER.format(job=job_name, table=table, stamp=stamp,
                              src_count=len(rows), note=job["note"],
                              batch=BATCH_ID, cols=job["cols"]))
        f.write(",\n".join(job["row"](r) for r in rows))
        f.write(FOOTER.format(table=table))

    print(f"{path}  ({len(rows)}행, {path.stat().st_size // 1024}KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
