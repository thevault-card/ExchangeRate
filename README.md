# market-data-collector

환율·시장지수 수집 서비스. TheVault 카드 시세의 원화 환산과 자산 비교 차트에 쓰이는 기준 데이터를 모읍니다.

| 파이프라인 | 대상 | 소스 |
|---|---|---|
| 환율 | USD → KRW 매매기준율 | 한국수출입은행 Open API |
| 시장지수 | S&P500 종가 | yfinance |
| 시장지수 | 코스피 종가 | 공공데이터포털 (금융위원회) |

지금은 독립 운용하되, 나중에 TheVault와 합류합니다. 그래서 스키마는 처음부터 TheVault 정의서 기준(`silver.fx_exchange_rates` 등)에 맞춥니다.

## 참고 문서

`docs/reference/` — TheVault에서 복사한 **스냅샷**입니다. 정본은 TheVault 쪽이므로 내용 변경은 그쪽에서 합니다.

- `환율_시장지수_수집파이프라인_스펙.md` (rev.2) — 구현 정본
- `01_카드마스터_시세_정의서_draft.md`
- `01_카드마스터_시세_ERD.md`

## 실행

```bash
uv sync
cp .env.example .env      # DATABASE_URL 채우기
uv run --env-file .env python -m collector index_spx
uv run --env-file .env python -m collector index_kospi
uv run --env-file .env python -m collector fx_daily      # EXIM_API_KEY 필요
```

## 초기 백필 (최초 1회만)

인자 없이 돌리면 최근 5일치만 받는다. 차트용 과거 구간은 한 번만 따로 받는다.

```bash
uv run --env-file .env python -m collector index_spx   1095   # 3년
uv run --env-file .env python -m collector index_kospi 1095
```

환율은 백필하지 않는다 — 원화 환산에 항상 최신 환율 1건만 쓰기 때문(스펙 §3-1 R-4).

## cron (KST)

| 배치 | cron |
|---|---|
| `index_spx` | `30 6 * * 2-6` |
| `index_kospi` | `40 15 * * 1-5` |
| `fx_daily` | `10 11 * * 1-5` |
| `fx_daily` (재확인) | `10 16 * * 1-5` |

서버 타임존이 UTC면 KST 기준으로 환산할 것.

## 상태

지수 2종 수집 동작. 환율은 `EXIM_API_KEY` 발급 후 검증 예정.
스키마는 `schema.sql`, 설계는 `docs/design/`, DB 현황은 `docs/db.md`.