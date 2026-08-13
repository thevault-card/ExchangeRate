# market-data-collector

환율·시장지수를 매일 수집해 PostgreSQL에 적재하는 배치 파이프라인입니다. TheVault 카드 시세의 원화 환산과 자산 비교 차트에 쓰이는 기준 데이터를 모읍니다. API 서버가 아니라 **cron이 부르는 배치 3개**가 전부입니다.

| 배치 | 대상 | 소스 | 적재 테이블 |
|---|---|---|---|
| `index_spx` | S&P500 종가 | yfinance `^GSPC` | `silver.market_indices` |
| `index_kospi` | 코스피 종가 | yfinance `^KS11` | `silver.market_indices` |
| `fx_daily` | USD·JPY 매매기준율 | 한국수출입은행 Open API | `silver.fx_exchange_rates` |

## 어떻게 도는가

```
스케줄러(06:30 / 18:30 KST)  →  scripts/run_all.cmd  →  배치 3개를 순서대로
                                                          │
  ┌───────────────────────────────────────────────────────┘
  │
  ├─ ① DB 조회: 있어야 할 세션 중 아직 없는 날짜는?
  │      없으면 → status=up_to_date, 외부 호출 0건, 종료코드 0
  │
  ├─ ② 있으면 수집 (재시도 3회, 실패 시 SourceError)
  ├─ ③ UPSERT — 값이 같으면 UPDATE를 건너뛴다(멱등)
  └─ ④ 신선도 검사 → 마감된 세션이 비어 있으면 종료코드 1
```

**핵심은 ①입니다.** 배치가 스스로 "받을 게 있는지"를 판단하므로, 스케줄이 어느 시각에 무엇을 부를지 고를 필요가 없습니다. 매번 셋 다 불러도 대부분 외부 호출 없이 끝납니다.

- **주말·공휴일**은 따로 처리하지 않습니다. 거래소 캘린더(`exchange_calendars`)가 "마감된 세션"을 정하고, 휴장이면 그 날짜가 애초에 후보에 없습니다.
- **하루 걸러도 자동 복구**됩니다. 검사 구간이 `min(최근 5일, 최신 적재일 + 1일)`이라 오래 멈췄으면 구간이 저절로 넓어집니다.
- **금액은 전부 `Decimal`** 입니다. `float`을 거치면 정밀도가 깨집니다. JPY는 100엔당 값이라 100으로 나눠 1엔당으로 정규화합니다.
- **잠정치는 확정값을 못 덮어씁니다.** 코스피는 yfinance 잠정값(`source='yfinance'`)이고, 나중에 공공데이터포털 확정값으로 교체할 예정입니다. UPSERT에 방향 규칙이 걸려 있어 역전되지 않습니다.

로그는 stdout에 JSON 한 줄씩 나갑니다.

```json
{"ts":"2026-08-13T18:30:04+09:00","job":"index_kospi","status":"success","fetched":4,"written":1,"latest_close":"6579.04"}
{"ts":"2026-08-13T18:30:07+09:00","job":"fx_daily","status":"up_to_date","latest_date":"2026-08-13"}
```

`status`: `success` / `up_to_date`(받을 것 없음) / `market_closed` / `skipped` / `rate_limited` — 여기까지는 종료코드 0. `failure`만 1입니다.

## 실행

```bash
uv sync
cp .env.example .env       # DATABASE_URL, EXIM_API_KEY, FX_ENABLED 채우기

uv run --env-file .env python -m collector index_spx
uv run --env-file .env python -m collector index_kospi
uv run --env-file .env python -m collector fx_daily

scripts/run_all.cmd        # 셋 다 (스케줄러가 부르는 진입점)
```

**일수를 붙이면 복구 실행**입니다. 스킵 판정을 건너뛰고 그 범위를 다시 받습니다.

```bash
uv run --env-file .env python -m collector index_spx 1095   # 3년치 초기 적재
uv run --env-file .env python -m collector fx_backfill 30   # 환율 과거 채우기
```

## 테스트

```bash
uv run --env-file .env.local pytest
```

`.env.local`은 **로컬 DB를 가리키는 별도 환경 파일**입니다. 테스트는 실제 DB에 붙어 쓰고 롤백하므로, 운영 DB를 가리키면 `conftest.py`가 실행 자체를 거부합니다.

## 구조

```
src/collector/
  config.py    환경변수·상수. 거래소 캘린더 매핑, 캘린더가 모르는 휴장일
  sources.py   외부 호출. 재시도·정밀도 변환·적재 불가 값 차단
  alerts.py    거래소 캘린더 판정 (마감된 세션, 신선도, 이상치)  ← 두뇌
  db.py        UPSERT 2개. 멱등 규칙과 잠정/확정 방향 규칙
  jobs.py      위를 배선. 결함이 있다면 대개 여기
```

- 설계: `docs/design/` — 왜 그렇게 만들었는지가 여기 있습니다
- 스키마: `schema.sql` · DB 현황: `docs/db.md`
- 정본 스펙: `docs/reference/` (TheVault에서 복사한 스냅샷, 변경은 그쪽에서)

## 상태 (2026-08-13)

- 지수 2종·환율 2통화 수집 동작 중. 하루 2회 자동 실행
- 적재 대상은 **공유 DB에 직접**입니다. 수동 업로드 단계는 없어졌습니다
- 남은 일: 상시 가동 서버(EC2) 이전, 실패 알림 채널 — `docs/design/2026-08-11-상시-실행-전환-설계.md` §6
