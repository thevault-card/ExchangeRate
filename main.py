"""환율·시장지수 수집 파이프라인 — 여기서 시작하세요.

    python main.py index_spx        S&P500 종가 (yfinance)
    python main.py index_kospi      코스피 종가 (yfinance)
    python main.py fx_daily         환율 USD·JPY (한국수출입은행)

    python main.py index_spx 1095   일수를 붙이면 복구 실행.
                                    스킵 판정을 건너뛰고 그 범위를 다시 받는다.

실행에는 환경 파일이 필요합니다:  uv run --env-file .env python main.py index_spx
운영에서는 cron 이 scripts/run_all.sh 를 부르고, 그게 결국 이 파일과 같은
함수(collector.jobs.run)를 부릅니다 — 손으로 돌려도 운영과 같은 경로입니다.


코드 지도 — 배치 하나가 도는 순서
=================================

    src/collector/jobs.py       ★ 배선. 여기부터 읽으면 전체가 보인다
        │
        ├─ rules.py             ① 언제 받아야 하나
        │                          거래소 캘린더로 "마감된 세션"을 정하고,
        │                          비어 있으면 실패로 판정한다
        │
        ├─ sources.py           ② 외부에서 받아온다
        │                          yfinance(지수) · 한국수출입은행(환율)
        │                          재시도·정밀도 변환·못 쓸 값 차단
        │
        ├─ db.py                ③ 적재한다
        │                          UPSERT. 값이 같으면 건너뛰고,
        │                          잠정치는 확정값을 못 덮어쓴다
        │
        ├─ config.py               설정·상수 (통화 매핑, 캘린더 매핑, 테이블명)
        └─ logs.py                 stdout 에 JSON 한 줄

    scripts/run_all.sh          cron 진입점 (morning=지수 / evening=코스피·환율)
    tests/                      test_<모듈명>.py 로 1:1 대응
    docs/design/                왜 그렇게 만들었는지
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from collector.jobs import run  # 위 sys.path 설정 뒤라야 import 된다

if __name__ == "__main__":
    job = sys.argv[1] if len(sys.argv) > 1 else ""
    # 인자를 안 주면 None — 평소 실행이라 스킵 판정을 거친다.
    days = int(sys.argv[2]) if len(sys.argv) > 2 else None
    raise SystemExit(run(job, days))
