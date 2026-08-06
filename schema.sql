-- ExchangeRate 수집 파이프라인 스키마
-- 기존 DB에 이미 만들어진 테이블 2개에 제약을 추가하고, gold 뷰를 만든다.
-- DBeaver에서 이 파일 전체를 실행한다. 여러 번 돌려도 안전하다.

CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- 1. 환율 -------------------------------------------------------------
-- PK 순서는 (currency_code, rate_date). 조회가 WHERE currency_code='USD'
-- ORDER BY rate_date DESC 라서 뒤집으면 인덱스를 못 탄다. (스펙 §1-4)
ALTER TABLE silver.fx_exchange_rates_test
  ALTER COLUMN "source"   SET NOT NULL,
  ALTER COLUMN created_at SET NOT NULL,
  ALTER COLUMN updated_at SET NOT NULL;

ALTER TABLE silver.fx_exchange_rates_test
  DROP CONSTRAINT IF EXISTS pk_fx_exchange_rates_test;
ALTER TABLE silver.fx_exchange_rates_test
  ADD CONSTRAINT pk_fx_exchange_rates_test PRIMARY KEY (currency_code, rate_date);

ALTER TABLE silver.fx_exchange_rates_test
  DROP CONSTRAINT IF EXISTS ck_fx_base_rate_positive;
ALTER TABLE silver.fx_exchange_rates_test
  ADD CONSTRAINT ck_fx_base_rate_positive CHECK (base_rate > 0);

-- 2. 시장지수 ---------------------------------------------------------
-- is_provisional = "나중에 공식 확정값으로 덮일 예정인가"
--   코스피(yfinance ^KS11) = true / S&P500(^GSPC) = false   (설계 §4-2)
ALTER TABLE silver.market_indices_test
  ADD COLUMN IF NOT EXISTS is_provisional boolean NOT NULL DEFAULT false;

ALTER TABLE silver.market_indices_test
  ALTER COLUMN "source"   SET NOT NULL,
  ALTER COLUMN created_at SET NOT NULL,
  ALTER COLUMN updated_at SET NOT NULL;

ALTER TABLE silver.market_indices_test
  DROP CONSTRAINT IF EXISTS pk_market_indices_test;
ALTER TABLE silver.market_indices_test
  ADD CONSTRAINT pk_market_indices_test PRIMARY KEY (index_code, trade_date);

ALTER TABLE silver.market_indices_test
  DROP CONSTRAINT IF EXISTS ck_market_close_positive;
ALTER TABLE silver.market_indices_test
  ADD CONSTRAINT ck_market_close_positive CHECK (close_value > 0);

-- 3. gold 뷰 — carry-forward (설계 §7) --------------------------------
-- 휴일에 장이 안 열려 값이 없는 날을 직전 거래일 값으로 채운다.
-- 코스피와 S&P500은 휴일이 서로 달라서, 안 채우면 한쪽만 구멍이 난다.
CREATE OR REPLACE VIEW gold.v_market_index_daily AS
WITH codes AS (
  SELECT DISTINCT index_code FROM silver.market_indices_test
),
cal AS (
  SELECT d::date AS calendar_date
    FROM generate_series(
           (SELECT MIN(trade_date) FROM silver.market_indices_test),
           CURRENT_DATE,
           INTERVAL '1 day'
         ) d
)
SELECT c.index_code,
       cal.calendar_date,
       m.close_value,
       m.trade_date                        AS source_trade_date,
       m.is_provisional,
       (m.trade_date <> cal.calendar_date) AS is_carried_forward
  FROM cal
 CROSS JOIN codes c
  LEFT JOIN LATERAL (
       SELECT close_value, trade_date, is_provisional
         FROM silver.market_indices_test s
        WHERE s.index_code = c.index_code
          AND s.trade_date <= cal.calendar_date
        ORDER BY s.trade_date DESC
        LIMIT 1
  ) m ON true;
