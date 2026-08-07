"""수집 데이터를 TheVault 작업 환경 DB 로 옮긴다.

    # 미리보기 (아무것도 쓰지 않는다)
    uv run --env-file .env python scripts/upload_to_thevault.py fx    --table silver.fx_exchange_rate
    uv run --env-file .env python scripts/upload_to_thevault.py index --table silver.market_indices

    # 실제 적재 (사용자 승인 후에만)
    ... --commit

기본은 **미리보기**다. --commit 없이는 한 행도 쓰지 않는다. TheVault DB 는 개발용
스크래치가 아니라 다른 사람의 작업이 얹힌 실환경이라, 실수로 도는 일이 없어야 한다.

적재 규칙 (2026-08-07 지시):

  fx     created_at / updated_at 을 **원본 값 그대로** 넣는다.
  index  created_at / updated_at 을 **적재 시점(now())** 으로 넣는다. 원본 날짜는 버린다.
  둘 다  created_batch_id / updated_batch_id = 'lkm'
  둘 다  이미 있는 키는 건드리지 않는다(ON CONFLICT DO NOTHING). 첫 적재라 덮어쓸
         이유가 없고, 남의 데이터를 조용히 바꾸는 게 더 위험하다.
"""
import argparse
import os
import sys

import psycopg

BATCH_ID = "lkm"

JOBS = {
    "fx": {
        "src": "silver.fx_exchange_rates_test",
        "key_cols": ["rate_date", "currency_code", "base_rate", "source"],
        "select": """SELECT rate_date, currency_code, base_rate, source, created_at, updated_at
                       FROM silver.fx_exchange_rates_test ORDER BY rate_date""",
        # 원본 시각을 그대로 넘긴다
        "insert_cols": ["rate_date", "currency_code", "base_rate", "source",
                        "created_at", "updated_at", "created_batch_id", "updated_batch_id"],
        "values_sql": "(%s, %s, %s, %s, %s, %s, %s, %s)",
        "keep_timestamps": True,
        "date_col": "rate_date",
    },
    "index": {
        "src": "silver.market_indices_test",
        "key_cols": ["index_code", "trade_date", "close_value", "source", "is_provisional"],
        "select": """SELECT index_code, trade_date, close_value, source, is_provisional
                       FROM silver.market_indices_test ORDER BY index_code, trade_date""",
        # created_at/updated_at 은 DB 의 now() 를 쓴다 — 적재 시점이 곧 생성 시점
        "insert_cols": ["index_code", "trade_date", "close_value", "source", "is_provisional",
                        "created_at", "updated_at", "created_batch_id", "updated_batch_id"],
        "values_sql": "(%s, %s, %s, %s, %s, now(), now(), %s, %s)",
        "keep_timestamps": False,
        "date_col": "trade_date",
    },
}


def target_dsn() -> str:
    missing = [k for k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD")
               if not os.environ.get(k)]
    if missing:
        raise SystemExit(f"환경변수가 없다: {', '.join(missing)}")
    return (f"postgresql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
            f"@{os.environ['DB_HOST']}:{os.environ['DB_PORT']}/{os.environ['DB_NAME']}")


def columns_of(conn, table: str) -> list[str]:
    schema, name = table.split(".", 1)
    return [r[0] for r in conn.execute(
        """SELECT column_name FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position""",
        (schema, name)).fetchall()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=sorted(JOBS))
    ap.add_argument("--table", required=True, help="대상 테이블 (schema.table)")
    ap.add_argument("--commit", action="store_true", help="실제로 쓴다. 없으면 미리보기")
    args = ap.parse_args()
    job = JOBS[args.job]

    src = psycopg.connect(os.environ["DATABASE_URL"])
    rows = src.execute(job["select"]).fetchall()
    if not rows:
        raise SystemExit(f"원본이 비어 있다: {job['src']}")
    print(f"원본 {job['src']}: {len(rows)}행")

    dst = psycopg.connect(target_dsn())
    cols = columns_of(dst, args.table)
    if not cols:
        raise SystemExit(f"대상 테이블이 없다: {args.table}")
    print(f"대상 {args.table}: {len(cols)}컬럼")

    missing = [c for c in job["insert_cols"] if c not in cols]
    if missing:
        raise SystemExit(
            f"대상에 없는 컬럼: {missing}\n"
            f"대상 컬럼: {cols}\n구조가 다르다. 적재를 중단한다.")

    before = dst.execute(f"SELECT count(*) FROM {args.table}").fetchone()[0]
    print(f"대상 현재 행 수: {before}")

    stamp = "원본 값 유지" if job["keep_timestamps"] else "적재 시점(now())"
    if not args.commit:
        print("\n[미리보기] --commit 이 없어 아무것도 쓰지 않았다.")
        print(f"  넣으려는 행: {len(rows)}  (이미 있는 키는 건너뜀)")
        print(f"  created_at/updated_at: {stamp}")
        print(f"  created_batch_id/updated_batch_id: '{BATCH_ID}'")
        print(f"  첫 행 예시: {rows[0]}")
        return 0

    sql = (f"INSERT INTO {args.table} ({', '.join(job['insert_cols'])}) "
           f"VALUES {job['values_sql']} ON CONFLICT DO NOTHING")
    with dst.cursor() as cur:
        for r in rows:
            cur.execute(sql, (*r, BATCH_ID, BATCH_ID))
    dst.commit()

    after = dst.execute(f"SELECT count(*) FROM {args.table}").fetchone()[0]
    print(f"\n적재 완료: {before} -> {after} (+{after - before}행)")
    print(f"  건너뜀(이미 존재): {len(rows) - (after - before)}행")
    print(f"  created_at/updated_at: {stamp} · batch_id: '{BATCH_ID}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
