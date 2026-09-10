import os

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="DATABASE_URL is not set")
def test_postgres_accepts_connection():
    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone() == (1,)
