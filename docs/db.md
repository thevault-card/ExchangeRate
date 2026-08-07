# DB 현황

> 기록일: 2026-08-06 · 출처: DBeaver에 정의된 실제 테이블 (DDL 그대로 옮김)
> 상태: **전용 데이터베이스 `exchangerate_dev`에 `schema.sql` 적용 완료** (2026-08-07). 수집 데이터는 기존 DB가 아니라 이 전용 DB에 담는다. 기존 DB의 `_test` 테이블 2개는 제약 없는 상태로 남겨두고 쓰지 않는다.

---

## 1. 현재 구조

> 아래는 **기존(옛) DB**에 있던 테이블의 기록이다. 지금 수집 파이프라인이 쓰는 전용 DB(`exchangerate_dev`)의 스키마는 `schema.sql`을 참고한다.

스키마 `silver`, 테이블 2개. 이름에 `_test` 접미사가 붙어 있다.

```sql
CREATE TABLE silver.fx_exchange_rates_test (
	rate_date        date          NOT NULL,
	currency_code    varchar(10)   NOT NULL,
	base_rate        numeric(12,4) NOT NULL,
	"source"         varchar(30)   NULL,
	created_at       timestamptz   DEFAULT now() NULL,
	updated_at       timestamptz   DEFAULT now() NULL,
	created_batch_id text          NULL,
	updated_batch_id text          NULL
);

CREATE TABLE silver.market_indices_test (
	index_code       varchar(10)   NOT NULL,
	trade_date       date          NOT NULL,
	close_value      numeric(14,2) NOT NULL,
	"source"         varchar(30)   NULL,
	created_at       timestamptz   DEFAULT now() NULL,
	updated_at       timestamptz   DEFAULT now() NULL,
	created_batch_id text          NULL,
	updated_batch_id text          NULL
);
```

컬럼명·타입·거버넌스 컬럼 4종은 스펙 §1-4 / §2-4와 일치한다. 제약(PK·CHECK·NOT NULL)만 아직 안 걸려 있다.

---

## 2. 스펙·설계와 일치하는 것

| 항목 | 확인 |
|---|---|
| 스키마명 `silver` | 스펙과 동일 → 이식 시 이름 바꿀 것 없음 |
| `base_rate numeric(12,4)` / `close_value numeric(14,2)` | 스펙과 동일. 금액은 `Decimal`로 다룬다는 전제와 맞음 |
| `timestamptz` | 스펙과 동일 (기존 TheVault silver의 naive `timestamp`와는 다름 → 미결 D-6) |
| 거버넌스 컬럼 4종 | 기존 `ppt_*` 패턴과 동일 |

---

## 3. 확정 사항 (2026-08-06 결정 · 2026-08-07 적용 완료)

아래 결정은 모두 `schema.sql`에 반영되어 `exchangerate_dev`에 적용됐다. 각 소절의 본문은 결정 당시의 문제 서술이므로 현재 시제로 읽지 않는다.

### 3-1. PK — 이건 선택이 아니라 전제

두 테이블 다 PK가 없다. 배치는 `INSERT … ON CONFLICT (…) DO UPDATE` 로 적재하는데, **충돌 대상이 될 PK나 UNIQUE가 없으면 이 구문 자체가 성립하지 않는다.** 같은 날 배치를 두 번 돌리면 행이 두 개 쌓인다(멱등성 붕괴).

```sql
PRIMARY KEY (currency_code, rate_date)   -- fx_exchange_rates
PRIMARY KEY (index_code, trade_date)     -- market_indices
```

**fx의 컬럼 순서를 뒤집지 말 것.** 실제 조회가 `WHERE currency_code='USD' ORDER BY rate_date DESC` 라서 `currency_code`가 앞이어야 인덱스를 탄다. 뒤집으면 풀스캔. (스펙 §1-4)

> 참고: 테이블의 컬럼 나열 순서와 PK 컬럼 순서는 별개다. 현재 DDL이 `rate_date`를 먼저 적어둔 것은 문제되지 않는다.

**결정:** 추가하기로 확정. `schema.sql`에 반영됨.

### 3-2. `is_provisional` 누락 (market_indices)

```sql
is_provisional boolean NOT NULL DEFAULT false
```

코스피를 yfinance로 받는 값은 공식 확정값이 아니라 **나중에 공공데이터포털 확정값으로 덮어쓸 예정**이다. 이 컬럼이 없으면 설계 §6의 덮어쓰기 방향 규칙(잠정은 확정을 못 건드리고, 확정은 항상 이김)이 성립하지 않는다.

| 적재 | 값 |
|---|---|
| 코스피 (yfinance `^KS11`) | `true` |
| S&P500 (yfinance `^GSPC`) | `false` |

**결정:** 추가하기로 확정. `schema.sql`에 반영됨.

### 3-3. NOT NULL · CHECK

`source`·`created_at`·`updated_at`이 NULL을 허용한다. 스펙은 셋 다 NOT NULL이다. 파싱 버그로 값이 조용히 비는 것을 DB에서 막는 자리다.

```sql
ALTER COLUMN "source" SET NOT NULL, ALTER COLUMN created_at SET NOT NULL, ALTER COLUMN updated_at SET NOT NULL;
CHECK (base_rate > 0)     -- fx
CHECK (close_value > 0)   -- market_indices
```

**결정:** 추가하기로 확정. `schema.sql`에 반영됨.

### 3-4. 테이블 이름

`_test` 접미사를 실제로 쓸 것인지, 아니면 검증용이고 실운영은 `fx_exchange_rates` / `market_indices` 인지. alembic 마이그레이션은 후자 기준으로 쓸 예정.

**결정:** `_test` 접미사를 유지하기로 확정(검증 단계). 전환 시 `config.py` 두 줄만 바꾼다.

### 3-5. `unit` 컬럼 (fx) — 넣을지 말지

```sql
unit smallint NOT NULL DEFAULT 1
```

v1은 USD 단일이라 항상 `1`이고 아무 동작도 안 한다. JPY를 붙일 때 필요해지는데(엔화는 100엔 기준), 그때 추가하면 마이그레이션 + 기존 뷰 수정이 따라온다. 지금 넣으면 컬럼 하나. (스펙 부록 A)

**결정:** 넣지 않기로 확정. v1은 USD 단일이라 항상 1이고, JPY 붙일 때 추가한다.

### 3-6. gold 스키마

carry-forward 뷰 `gold.v_market_index_daily`(설계 §7)가 들어갈 `gold` 스키마가 아직 없다.

**결정:** `schema.sql`에서 `CREATE SCHEMA IF NOT EXISTS gold`로 생성. carry-forward 뷰도 함께 반영됨.

---

## 4. 아직 없는 것

| 대상 | 상태 |
|---|---|
| `gold` 스키마 및 뷰 | **적용 완료** (2026-08-07, `exchangerate_dev`). `gold.v_market_index_daily` 존재, 데이터 없어 0행 |
| alembic 마이그레이션 | 미작성. 원시 SQL(`schema.sql`)로 충분해 도입 보류 (계획 Tech Stack 참고) |
| 접속 정보 | `DATABASE_URL` 로 단일화 완료. `exchangerate_dev`(포트 4123, 사용자 `postgres`) 가리킴, `.env`에 설정됨 |

---

## 참고

- 설계: `docs/design/2026-08-05-지수-수집-파이프라인-설계.md`
- 정본 스펙: `docs/reference/환율_시장지수_수집파이프라인_스펙.md` (rev.2)
