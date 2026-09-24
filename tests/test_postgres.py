"""Дымовой тест: база доступна.

Приехал с PR #2, когда `conftest.py` ещё не было, и читал DATABASE_URL под
собственным skipif. Поэтому набор тестов показывал один skip, пока всё
остальное выполнялось. Проверка та же, но на общем маркере и фикстуре.
"""

import pytest
from sqlalchemy import text

from .conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]


def test_postgres_accepts_connection(engine):
    with engine.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
