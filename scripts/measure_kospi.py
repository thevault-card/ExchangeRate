"""코스피 종가가 언제 확정되는지 실측한다 (설계 §13 B-3 / A-3).

배치는 15:40 에 도는데 KRX 마감이 15:30 이다. 그 10분 뒤 yfinance 가 주는 값이
확정 종가인지, 아니면 아직 움직이는 장중값인지 아무도 모른다. 알아내려면
마감 직후부터 여러 번 떠서 값이 언제부터 고정되는지 보는 수밖에 없다.

한 번 호출하면 지금 값을 CSV 에 한 줄 덧붙인다. 스케줄러가 15:35~16:25 사이
10분마다 부르면 하루치 표본이 모인다. 5거래일 모으면 판단할 수 있다.

    uv run --env-file .env python scripts/measure_kospi.py

거래일이 아니면 아무것도 하지 않는다.
"""
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import exchange_calendars as xcals
import yfinance as yf

from collector.config import KST

OUT = Path(__file__).resolve().parent.parent / "measurements" / "kospi_close_samples.csv"
FIELDS = ["sampled_at", "trade_date", "close", "open", "high", "low", "volume"]


def main() -> int:
    now = datetime.now(KST)
    today = now.date()

    if not xcals.get_calendar("XKRX").is_session(today):
        print(json.dumps({"event": "skip", "reason": "not_a_session", "date": str(today)}))
        return 0

    df = yf.download("^KS11", start=today, interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        row = {"sampled_at": now.isoformat(), "trade_date": "", "close": ""}
    else:
        # 컬럼이 MultiIndex 로 오는 버전이 있어 첫 열을 집는다.
        def pick(name: str):
            if name not in df:
                return ""
            col = df[name]
            if hasattr(col, "columns"):
                col = col.iloc[:, 0]
            return col.iloc[-1]

        row = {
            "sampled_at": now.isoformat(),
            "trade_date": str(df.index[-1].date()),
            "close": pick("Close"),
            "open": pick("Open"),
            "high": pick("High"),
            "low": pick("Low"),
            "volume": pick("Volume"),
        }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    is_new = not OUT.exists()
    with OUT.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if is_new:
            w.writeheader()
        w.writerow(row)

    print(json.dumps({"event": "sample", **{k: str(v) for k, v in row.items()}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
