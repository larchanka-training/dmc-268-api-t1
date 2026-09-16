"""Smoke test: the database is reachable.

It arrived with PR #2, before `conftest.py` existed, reading DATABASE_URL
behind a skipif of its own. That is why the suite reported one skip while
everything else ran. Same assertion, on the shared marker and fixture.
"""

import pytest
from sqlalchemy import text

from .conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


def test_postgres_accepts_connection(engine):
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
