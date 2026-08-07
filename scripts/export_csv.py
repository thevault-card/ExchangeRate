"""수집 데이터를 CSV 로 뽑는다. 남에게 전달하거나 Excel 로 볼 때 쓴다.

    uv run --env-file .env python scripts/export_csv.py

export/ 에 4개 파일이 생긴다. 세 CSV 는 UTF-8 BOM 으로 쓴다 — BOM 이 없으면
한글 Windows 의 Excel 이 UTF-8 을 cp949 로 읽어 글자가 깨진다.
"""
import csv
import os
import sys
from datetime import date
from pathlib import Path

import psycopg

OUT = Path(__file__).resolve().parent.parent / "export"

# 차트용 가로형. 날짜 한 줄에 세 값이 다 있어서 Excel 에서 범위 잡고 바로 그래프가 된다.
# 휴일은 직전 거래일 값으로 채운다 — 안 채우면 코스피와 S&P500 의 휴일이 달라
# 한쪽만 구멍이 나고 선이 끊긴다.
WIDE_SQL = """
WITH cal AS (
  SELECT d::date AS calendar_date
    FROM generate_series(
           (SELECT min(trade_date) FROM silver.market_indices_test),
           CURRENT_DATE, INTERVAL '1 day') d
)
SELECT cal.calendar_date,
       k.close_value AS kospi,        (k.trade_date <> cal.calendar_date) AS kospi_carried,
       s.close_value AS spx,          (s.trade_date <> cal.calendar_date) AS spx_carried,
       f.base_rate   AS usd_krw,      (f.rate_date  <> cal.calendar_date) AS usd_krw_carried
  FROM cal
  LEFT JOIN LATERAL (SELECT close_value, trade_date FROM silver.market_indices_test m
                      WHERE m.index_code='KOSPI' AND m.trade_date <= cal.calendar_date
                      ORDER BY m.trade_date DESC LIMIT 1) k ON true
  LEFT JOIN LATERAL (SELECT close_value, trade_date FROM silver.market_indices_test m
                      WHERE m.index_code='SPX' AND m.trade_date <= cal.calendar_date
                      ORDER BY m.trade_date DESC LIMIT 1) s ON true
  LEFT JOIN LATERAL (SELECT base_rate, rate_date FROM silver.fx_exchange_rates_test x
                      WHERE x.currency_code='USD' AND x.rate_date <= cal.calendar_date
                      ORDER BY x.rate_date DESC LIMIT 1) f ON true
 ORDER BY cal.calendar_date
"""

# 원본 두 파일은 테이블 컬럼을 전부 내보낸다. 거버넌스 컬럼(created_at·batch_id)까지
# 포함해야 DDL 과 1:1 로 대조가 된다.
INDEX_SQL = """
SELECT * FROM silver.market_indices_test ORDER BY index_code, trade_date
"""

FX_SQL = """
SELECT * FROM silver.fx_exchange_rates_test ORDER BY rate_date
"""

README = """수집 데이터 (ExchangeRate)

내려받은 날: {today}

■ 파일
  market_daily_wide.csv   차트용. 날짜 한 줄에 코스피·S&P500·환율이 다 있음.
                          Excel 에서 A~F 열 잡고 바로 꺾은선 그래프가 됨.
  market_indices.csv      지수 원본. 실제 거래일만 있음(휴일 행 없음).
  fx_rates.csv            환율 원본. 실제 고시일만 있음.

■ 기간
  {period}

■ 꼭 알아야 할 것

  1. 코스피 값은 "잠정치"입니다.
     yfinance(Yahoo Finance) 에서 받은 값이며 한국거래소 공식 확정값이 아닙니다.
     market_indices.csv 의 is_provisional 열이 true 인 것이 그 표시입니다.
     나중에 공공데이터포털 공식값으로 교체할 예정입니다.

  2. S&P500 도 yfinance 입니다.
     Yahoo 약관상 상업적 재배포가 허용되지 않습니다. 내부 검토용으로만 쓰세요.

  3. 환율은 한국수출입은행 공식 매매기준율입니다. 이건 공식값입니다.

  4. _carried 열이 true 면 그날 장이 안 열려서 직전 거래일 값을 그대로 채운 것입니다.
     실제로 그날 거래된 값이 아닙니다. 코스피와 S&P500 은 휴일이 서로 다릅니다
     (추석 vs 추수감사절). 채우지 않으면 한쪽만 구멍이 나서 그래프 선이 끊깁니다.

  5. 날짜는 각 거래소의 현지 거래일입니다. 한국 시간으로 변환하지 않았습니다.
     "S&P500 의 8월 1일 종가" 는 미국의 8월 1일 종가입니다.
"""


def dump(conn, sql: str, path: Path) -> int:
    with conn.cursor() as cur:
        cur.execute(sql)
        cols = [d.name for d in cur.description]
        rows = cur.fetchall()
    # utf-8-sig = BOM 포함. Excel 이 UTF-8 로 인식하게 하려면 필요하다.
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)
    return len(rows)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    conn = psycopg.connect(os.environ["DATABASE_URL"])

    counts = {
        "market_daily_wide.csv": dump(conn, WIDE_SQL, OUT / "market_daily_wide.csv"),
        "market_indices.csv": dump(conn, INDEX_SQL, OUT / "market_indices.csv"),
        "fx_rates.csv": dump(conn, FX_SQL, OUT / "fx_rates.csv"),
    }

    period = conn.execute(
        "SELECT min(trade_date), max(trade_date) FROM silver.market_indices_test"
    ).fetchone()
    (OUT / "README.txt").write_text(
        README.format(today=date.today().isoformat(), period=f"{period[0]} ~ {period[1]}"),
        encoding="utf-8-sig",
    )

    for name, n in counts.items():
        print(f"{name}: {n}행")
    print(f"README.txt")
    print(f"\n-> {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
