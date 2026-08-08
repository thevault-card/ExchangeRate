-- ExchangeRate 수집 파이프라인 스키마
-- 전용 데이터베이스(exchangerate_dev)에서 이 파일 전체를 실행한다.
-- 여러 번 돌려도 안전하다.
--
-- 컬럼 정의는 DBeaver 에서 만들었던 원본(docs/db.md §1)과 같고, 거기 없던
-- 제약(PK·CHECK·NOT NULL)이 더해져 있다. 컬럼 구성은 대상 DB(vaultdb silver)와 1:1 이다.

CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- 1. 환율 -------------------------------------------------------------
-- PK 순서는 (currency_code, rate_date). 조회가 WHERE currency_code='USD'
-- ORDER BY rate_date DESC 라서 뒤집으면 인덱스를 못 탄다. (스펙 §1-4)
-- 컬럼 나열 순서와 PK 컬럼 순서는 별개다.
CREATE TABLE IF NOT EXISTS silver.fx_exchange_rates_test (
    rate_date        date          NOT NULL,
    currency_code    varchar(10)   NOT NULL,
    base_rate        numeric(12,4) NOT NULL,
    "source"         varchar(30)   NOT NULL,
    created_at       timestamptz   NOT NULL DEFAULT now(),
    updated_at       timestamptz   NOT NULL DEFAULT now(),
    created_batch_id text,
    updated_batch_id text,
    CONSTRAINT pk_fx_exchange_rates_test PRIMARY KEY (currency_code, rate_date),
    -- PostgreSQL 은 numeric NaN 을 자기 자신과 '같다'고 보고 0보다 '크다'고도 보므로
    -- (IEEE 부동소수와 다름) `> 0` 만으로는 NaN 을 못 거른다. `< 'Infinity'` 를 더해야
    -- NaN 이 확실히 막힌다 (실측: PostgreSQL 15).
    CONSTRAINT ck_fx_base_rate_positive CHECK (base_rate > 0 AND base_rate < 'Infinity'::numeric)
);

-- 2. 시장지수 ---------------------------------------------------------
-- 코스피는 yfinance 잠정치라 나중에 공공데이터포털 확정값으로 교체할 예정인데,
-- 그 구분은 source 컬럼으로 한다(2026-08-08). 대상 DB 에 별도 표시 컬럼이 없다.
CREATE TABLE IF NOT EXISTS silver.market_indices_test (
    index_code       varchar(10)   NOT NULL,
    trade_date       date          NOT NULL,
    close_value      numeric(14,2) NOT NULL,
    "source"         varchar(30)   NOT NULL,
    created_at       timestamptz   NOT NULL DEFAULT now(),
    updated_at       timestamptz   NOT NULL DEFAULT now(),
    created_batch_id text,
    updated_batch_id text,
    CONSTRAINT pk_market_indices_test PRIMARY KEY (index_code, trade_date),
    -- NaN 배제 이유는 위 ck_fx_base_rate_positive 주석과 같다.
    CONSTRAINT ck_market_close_positive CHECK (close_value > 0 AND close_value < 'Infinity'::numeric)
);

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
       (m.trade_date <> cal.calendar_date) AS is_carried_forward
  FROM cal
 CROSS JOIN codes c
  LEFT JOIN LATERAL (
       SELECT close_value, trade_date
         FROM silver.market_indices_test s
        WHERE s.index_code = c.index_code
          AND s.trade_date <= cal.calendar_date
        ORDER BY s.trade_date DESC
        LIMIT 1
  ) m ON true;
