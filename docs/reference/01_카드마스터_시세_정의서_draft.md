# 카드 마스터 + 시세 정의서 (통합 초안, 검토용)

> `01_카드마스터_정의서_draft.md` + `02_시세_정의서_draft.md` 통합.

---

## 0. 전체 구조

```
[bronze] 기존 4개 (손대지 않음)
   ppt_pipeline_runs · ppt_raw_cards · ppt_raw_population · ppt_raw_sets
        ↓
[silver] 기존 8개 (손대지 않음)              [silver] 신규 4개
   ppt_cards ★          ppt_card_population ★    card_name_ko
   ppt_sets ★            ppt_card_price_history ★  fx_exchange_rates
   ppt_cards_priced      ppt_card_ebay_listings ★  market_indices
   ppt_card_ebay_price_history(미사용)              price_outlier_rules
   ppt_card_ebay_sales ★
        └──────────────────┬─────────────────────────┘
                            ↓
[gold] 뷰 5개 (SQL, 물리 저장 없음)
   V_card_price_timeline · V_card_price_change · V_card_trades · V_card_price_depth · V_card_population
```
★ = gold 뷰가 실제로 참조하는 테이블

---

## 1. 기존 파이프라인 테이블 — 현재 적재됨, 그대로 사용 (12개)

### 1-1. bronze (4개)

**`bronze.ppt_pipeline_runs`** — 수집파이프라인 실행이력
**화면 매핑**: - (파이프라인 내부, 화면 없음)

| 컬럼 | 타입 | Key |
|---|---|---|
| job_name | VARCHAR(30) | PK |
| run_date | DATE | PK |
| language | VARCHAR(20) | PK |
| status | VARCHAR(20) | |
| cursor_value | TEXT | |
| credits_consumed | INTEGER | |
| stats | JSONB | |
| started_at / finished_at | TIMESTAMPTZ | |
| created_at / updated_at / created_batch_id / updated_batch_id | | |

**`bronze.ppt_raw_cards`** — 원본 카드정보 (`ppt_cards`의 소스)
**화면 매핑**: - (파이프라인 내부, 화면 없음)

| 컬럼 | 타입 | Key |
|---|---|---|
| tcg_player_id | TEXT | PK |
| raw_json | JSONB | NOT NULL |
| source | TEXT | 예: `pokemon_price_tracker_api` |
| created_at / updated_at / created_batch_id / updated_batch_id | | |

**`bronze.ppt_raw_population`** — 원본 등급판정정보 (`ppt_card_population`의 소스)
**화면 매핑**: - (파이프라인 내부, 화면 없음)

| 컬럼 | 타입 | Key |
|---|---|---|
| tcg_player_id | VARCHAR(50) | PK |
| raw_json | JSONB | NOT NULL |
| created_at / updated_at / created_batch_id / updated_batch_id | | |

**`bronze.ppt_raw_sets`** — 원본 카드세트정보 (`ppt_sets`의 소스)
**화면 매핑**: - (파이프라인 내부, 화면 없음)

| 컬럼 | 타입 | Key |
|---|---|---|
| set_id | VARCHAR(50) | PK |
| raw_json | JSONB | NOT NULL |
| created_at / updated_at / created_batch_id / updated_batch_id | | |

---

### 1-2. silver (8개)

**`silver.ppt_cards`** ★ 카드 마스터로 그대로 사용
**화면 매핑**: [W02·W03] 카드 목록/상세 데이터

| 컬럼 | 타입 | Key |
|---|---|---|
| tcg_player_id | VARCHAR(50) | PK |
| set_id | VARCHAR(50) | |
| set_name | VARCHAR(200) | |
| name | VARCHAR(200) | 영문/로마자 |
| card_number | VARCHAR(50) | |
| rarity | VARCHAR(50) | |
| language | VARCHAR(20) | "일본판 유통가격" 의미 |
| market_price / low_price | NUMERIC(10,2) | 스냅샷값(시세는 gold 뷰 사용 권장) |
| image_url / image_cdn_url | VARCHAR(500) | |
| hp | INTEGER | |
| stage | VARCHAR(50) | |
| artist | TEXT | |
| attacks | JSONB | |
| weakness / resistance | VARCHAR(50) | |
| retreat_cost | INTEGER | |
| flavor_text | TEXT | |
| population_checked_at | TIMESTAMPTZ | |
| created_at / updated_at / created_batch_id / updated_batch_id | | |

**`silver.ppt_cards_priced`**(뷰) — ⚠️ 조건부. 적재 필터 용도로만, 앱 미참조. 컬럼은 `ppt_cards`와 동일(PK 없음)
**화면 매핑**: - (적재 필터용, 화면 없음)

**`silver.ppt_sets`** ★ 확장팩 마스터로 그대로 사용
**화면 매핑**: [W02·W03] 세트 정보

| 컬럼 | 타입 | Key |
|---|---|---|
| set_id | VARCHAR(50) | PK |
| name | VARCHAR(200) | |
| series | VARCHAR(100) | |
| card_count | INTEGER | |
| language | VARCHAR(20) | |
| release_date | DATE | |
| image_url / image_cdn_url | VARCHAR(500) | |
| cards_fetched_at | TIMESTAMPTZ | |
| created_at / updated_at / created_batch_id / updated_batch_id | | |

**`silver.ppt_card_population`** ★ `V_card_population`의 소스
**화면 매핑**: [W03] "PSA Population" 수치

| 컬럼 | 타입 | Key |
|---|---|---|
| tcg_player_id | VARCHAR(50) | PK |
| grader | VARCHAR(20) | PK |
| total_population | INTEGER | |
| gem_rate | FLOAT8 | |
| grade_breakdown | JSONB | |
| created_at / updated_at / created_batch_id / updated_batch_id | | |

**`silver.ppt_card_price_history`** ★ `V_card_price_timeline`의 소스(TCGPlayer), 690만건
**화면 매핑**: [W02·W03] 시세 그래프 재료

| 컬럼 | 타입 | Key |
|---|---|---|
| tcg_player_id | VARCHAR(50) | PK |
| condition | VARCHAR(50) | PK — 예: Near Mint |
| date | DATE | PK |
| market | NUMERIC(10,2) | |
| created_at / updated_at / created_batch_id / updated_batch_id | | |

**`silver.ppt_card_ebay_listings`** ★ `V_card_price_timeline`/`V_card_trades`/`V_card_price_depth`의 소스(eBay), 23.7만건
**화면 매핑**: [W02·W03] 최근체결·가격분포 재료

| 컬럼 | 타입 | Key |
|---|---|---|
| listing_id | VARCHAR(64) | PK |
| tcg_player_id | VARCHAR(50) | NOT NULL |
| grade_key | VARCHAR(30) | 예: psa10 |
| title | TEXT | |
| price | NUMERIC(10,2) | |
| sold_date | DATE | |
| url | VARCHAR(500) | |
| listing_type | VARCHAR(30) | |
| best_offer_accepted | BOOLEAN | |
| currency | VARCHAR(10) | |
| grading_company | VARCHAR(20) | |
| grade | VARCHAR(10) | |
| created_at / updated_at / created_batch_id / updated_batch_id | | |

**`silver.ppt_card_ebay_price_history`** — ⛔ 미사용. `ppt_card_ebay_listings` 집계로 대체 가능해 중복
**화면 매핑**: - (미사용)

**`silver.ppt_card_ebay_sales`** ★ 스마트 추정시세로 그대로 노출 예정 (새 테이블 아님)
**화면 매핑**: [W03] 스마트추정시세 (추후 노출 예정)

| 컬럼 | 타입 | Key |
|---|---|---|
| tcg_player_id | VARCHAR(50) | PK |
| grade_key | VARCHAR(30) | PK |
| grading_company | VARCHAR(20) | |
| count | INTEGER | |
| total_value / average_price / median_price / min_price / max_price | NUMERIC(10,2) | |
| market_price_7day | NUMERIC(10,2) | |
| market_trend | VARCHAR(20) | up/down/stable |
| last_sale_date | DATE | |
| smart_market_price | JSONB | `{price, method, daysUsed, confidence}` |
| created_at / updated_at / created_batch_id / updated_batch_id | | |

---

## 2. 신규 추가 — silver (4개)

**`silver.card_name_ko`** — 카드 한글명·별칭
**화면 매핑**: [W02] 검색바 - 카드명 검색

```sql
CREATE TABLE silver.card_name_ko (
  name_id           BIGSERIAL PRIMARY KEY,
  card_id           VARCHAR(50) NOT NULL REFERENCES silver.ppt_cards(tcg_player_id),
  name_type         VARCHAR(20),      -- 'official' | 'alias'
  card_name         VARCHAR(255) NOT NULL,
  source            VARCHAR(50),      -- 'pokeapi' | 'crawl_kr' | 'manual' | 'user_report'
  priority          INT DEFAULT 1,
  is_searchable     BOOLEAN DEFAULT true,
  created_at        TIMESTAMPTZ,
  updated_at        TIMESTAMPTZ,
  created_batch_id  TEXT,
  updated_batch_id  TEXT
);
```
적재: PokéAPI(76%) → 한국 공식사이트 크롤링(상위500, 93%+) → 운영자/유저 별칭.

**`silver.fx_exchange_rates`** — 환율 (상세: `환율_시장지수_수집파이프라인_스펙.md`)
**화면 매핑**: [W02·W03] 가격(₩) 표시 전체

```sql
CREATE TABLE silver.fx_exchange_rates (
  rate_date         DATE NOT NULL,
  currency_code     VARCHAR(10) NOT NULL,   -- 'USD' | 'JPY'
  base_rate         NUMERIC(12,4) NOT NULL, -- 매매기준율
  source            VARCHAR(30),            -- 'koreaexim'
  created_at        TIMESTAMPTZ,
  updated_at        TIMESTAMPTZ,
  created_batch_id  TEXT,
  updated_batch_id  TEXT,
  PRIMARY KEY (rate_date, currency_code)
);
```

**`silver.market_indices`** — 시장지수 (상세: `환율_시장지수_수집파이프라인_스펙.md`)
**화면 매핑**: [W07 대시보드] "두 지수 모두 이겼어요" 밑 비교 그래프

```sql
CREATE TABLE silver.market_indices (
  index_code        VARCHAR(10) NOT NULL,   -- 'SPX' | 'KOSPI'
  trade_date        DATE NOT NULL,
  close_value       NUMERIC(14,2) NOT NULL,
  source            VARCHAR(30),            -- 'yahoo_finance'
  created_at        TIMESTAMPTZ,
  updated_at        TIMESTAMPTZ,
  created_batch_id  TEXT,
  updated_batch_id  TEXT,
  PRIMARY KEY (index_code, trade_date)
);
```

**`silver.price_outlier_rules`** — 이상치 필터 기준값
**화면 매핑**: - (수집단계 설정, 화면 없음)

```sql
CREATE TABLE silver.price_outlier_rules (
  rule_id           SERIAL PRIMARY KEY,
  scope             VARCHAR(50) DEFAULT 'GLOBAL',
  threshold_pct     NUMERIC(5,2) NOT NULL DEFAULT 50.00,  -- 중앙값 대비 허용 편차(%)
  is_active         BOOLEAN DEFAULT true,
  created_at        TIMESTAMPTZ,
  updated_at        TIMESTAMPTZ
);
```
✅ 초기값 **±50%** 권장. 자릿수 오류(-90%대)·소수점 오류는 걸러내고, 실제 시세 급등락(대부분 ±20%대)은 정상 취급하는 안전한 값. 운영 중 데이터 보고 조정 가능.

---

## 3. 신규 추가 — gold (뷰 5개, SQL)

**`gold.V_card_price_timeline`** — 일별 시세 통합 (TCGPlayer+eBay, KRW 포함)
**화면 매핑**: [W03] "시세 그래프" 그림

```sql
CREATE VIEW gold.V_card_price_timeline AS
SELECT p.card_id, p.recorded_date, p.grade_condition, p.data_source,
       p.market_price, p.transaction_volume, p.currency,
       ROUND(p.market_price * fx.base_rate) AS market_price_krw
FROM (
  SELECT tcg_player_id AS card_id, date AS recorded_date, condition AS grade_condition,
         'TCGPlayer' AS data_source, market AS market_price, NULL::int AS transaction_volume,
         'USD' AS currency
  FROM silver.ppt_card_price_history
  UNION ALL
  SELECT tcg_player_id, sold_date, grade_key,
         'eBay', AVG(price), COUNT(*),
         'USD'
  FROM silver.ppt_card_ebay_listings
  GROUP BY tcg_player_id, sold_date, grade_key
) p
LEFT JOIN LATERAL (
  SELECT base_rate FROM silver.fx_exchange_rates
  WHERE currency_code = 'USD' AND rate_date <= p.recorded_date
  ORDER BY rate_date DESC LIMIT 1
) fx ON true;
```

**`gold.V_card_price_change`** 🆕 — 현재가·변동률·30일거래량 (홈 급상승 TOP5, 마켓 정렬용)
**화면 매핑**: [W01] "지금 급상승" 리스트 / [W02] 등락률순 정렬

> 근거: `GET /cards/trending?limit=5`, 마켓 정렬 옵션 "등락률순"/"인기순", 카드 상세 "최근 30일 거래 N건"

```sql
CREATE VIEW gold.V_card_price_change AS
WITH latest AS (
  SELECT DISTINCT ON (card_id, grade_condition)
         card_id, grade_condition, recorded_date, market_price, market_price_krw
  FROM gold.V_card_price_timeline
  ORDER BY card_id, grade_condition, recorded_date DESC
),
prev AS (
  SELECT DISTINCT ON (t.card_id, t.grade_condition)
         t.card_id, t.grade_condition, t.market_price AS prev_price
  FROM gold.V_card_price_timeline t
  JOIN latest l ON l.card_id = t.card_id AND l.grade_condition = t.grade_condition
  WHERE t.recorded_date < l.recorded_date
  ORDER BY t.card_id, t.grade_condition, t.recorded_date DESC
),
volume AS (
  SELECT card_id, grade_condition, SUM(transaction_volume) AS trades_30d
  FROM gold.V_card_price_timeline
  WHERE recorded_date >= CURRENT_DATE - INTERVAL '30 days'
  GROUP BY card_id, grade_condition
)
SELECT l.card_id, l.grade_condition,
       l.market_price AS current_price, l.market_price_krw AS current_price_krw,
       ROUND(((l.market_price - p.prev_price) / NULLIF(p.prev_price,0)) * 100, 2) AS change_pct,
       COALESCE(v.trades_30d, 0) AS trades_30d
FROM latest l
LEFT JOIN prev p ON p.card_id = l.card_id AND p.grade_condition = l.grade_condition
LEFT JOIN volume v ON v.card_id = l.card_id AND v.grade_condition = l.grade_condition;
```

⚠️ **한계**: `trades_30d`는 `transaction_volume`을 합산하는데, **TCGPlayer 쪽은 이 값이 원천적으로 NULL**입니다 (건수 정보 자체가 없음). 그래서 `trades_30d`·"인기순" 정렬은 **eBay 체결만 반영**됩니다. TCGPlayer는 시세(가격)는 주지만 몇 건 거래됐는지는 안 줍니다 — 데이터 원천의 한계이며 해결 방법 없음, 그대로 인지만 하고 갑니다.

**`gold.V_card_trades`** — 최근 체결 리스트 (패스스루)
**화면 매핑**: [W03] "최근 체결" 탭

```sql
CREATE VIEW gold.V_card_trades AS
SELECT tcg_player_id AS card_id, grade_key AS grade_condition,
       price, sold_date, grading_company
FROM silver.ppt_card_ebay_listings;
```

**`gold.V_card_price_depth`** — 가격대별 체결 분포 (기준가 ±6%, 7단계)
**화면 매핑**: [W03] "가격대별 체결" 탭 (호가창)

✅ 기준가는 **최신 시세**(`V_card_price_timeline`의 카드+등급별 가장 최근 날짜 값) 채택.

```sql
CREATE VIEW gold.V_card_price_depth AS
WITH base AS (
  SELECT card_id, grade_condition, market_price AS base_price
  FROM gold.V_card_price_timeline t
  WHERE recorded_date = (
    SELECT MAX(recorded_date) FROM gold.V_card_price_timeline t2
    WHERE t2.card_id = t.card_id AND t2.grade_condition = t.grade_condition
  )
)
SELECT l.tcg_player_id AS card_id, l.grade_key AS grade_condition,
       ROUND(b.base_price * (1 + step.n * 0.02) / 1000) * 1000 AS price_bucket,
       COUNT(*) AS qty,
       (step.n = 0) AS is_current
FROM silver.ppt_card_ebay_listings l
JOIN base b ON b.card_id = l.tcg_player_id AND b.grade_condition = l.grade_key
JOIN LATERAL (SELECT generate_series(-3, 3) AS n) step
  ON ABS(l.price - b.base_price * (1 + step.n * 0.02))
   = (SELECT MIN(ABS(l.price - b.base_price * (1 + n2 * 0.02))) FROM generate_series(-3,3) n2)
GROUP BY l.tcg_player_id, l.grade_key, step.n, b.base_price;
```

**`gold.V_card_population`** — PSA 등급판정 현황 (패스스루)
**화면 매핑**: [W03] "PSA Population" 수치

```sql
CREATE VIEW gold.V_card_population AS
SELECT tcg_player_id AS card_id, grader, total_population, gem_rate, grade_breakdown
FROM silver.ppt_card_population;
```

---

## 4. 결정 완료

| # | 항목 | 결정 |
|---|---|---|
| 1 | `V_card_price_depth` 기준가 | ✅ 최신 시세 채택 |
| 2 | `price_outlier_rules.threshold_pct` 초기값 | ✅ ±50% |

카드 마스터 + 시세 설계 완료. 남은 결정 없음.

## 다음
3단계(회원·인증)로 넘어갑니다.
