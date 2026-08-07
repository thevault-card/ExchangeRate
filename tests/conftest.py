import psycopg
import pytest

from collector.config import DATABASE_URL


@pytest.fixture
def conn():
    """실제 DB에 붙되, 테스트가 끝나면 롤백해서 아무것도 남기지 않는다."""
    c = psycopg.connect(DATABASE_URL)
    try:
        yield c
    finally:
        c.rollback()
        c.close()
