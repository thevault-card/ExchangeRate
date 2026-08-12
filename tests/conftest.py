import psycopg
import pytest

from collector.config import DATABASE_URL

# 테스트는 실제 DB 에 붙어서 쓰고 롤백한다. test_jobs.py 는 2099-01-01 같은 행을
# 진짜로 INSERT 한다. 테이블 이름에서 _test 접미사를 뗀 뒤로는(2026-08-12) 로컬 DB 와
# 적재 대상(vaultdb)이 이름까지 같아, 실수로 DATABASE_URL 만 바꿔도 pytest 가 남의
# 실환경에 트랜잭션을 연다. 이름이 못 막으니 여기서 막는다.
_FORBIDDEN = ("rds.amazonaws.com",)


def pytest_configure():
    for mark in _FORBIDDEN:
        if mark in DATABASE_URL:
            raise pytest.UsageError(
                f"DATABASE_URL 이 운영 DB({mark}) 를 가리킵니다. 테스트는 로컬 DB 로만 "
                f"돌립니다: uv run --env-file .env.local pytest"
            )


@pytest.fixture
def conn():
    """실제 DB에 붙되, 테스트가 끝나면 롤백해서 아무것도 남기지 않는다."""
    c = psycopg.connect(DATABASE_URL)
    try:
        yield c
    finally:
        c.rollback()
        c.close()
