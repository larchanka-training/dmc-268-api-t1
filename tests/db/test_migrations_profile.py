"""Миграция профиля: обратимость и согласованность с моделями.

Проверяется на живой базе: upgrade → downgrade → upgrade должен проходить
без мусора, как того требует database-migrations.
"""

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command

from ..conftest import TEST_DATABASE_URL, requires_db

pytestmark = [pytest.mark.integration, requires_db]


def _config() -> Config:
    config = Config("alembic.ini")
    # Как и в conftest: явно направляем миграции на тестовую базу, чтобы
    # env.py не пошёл читать Settings, которого у теста нет.
    assert TEST_DATABASE_URL is not None
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


def test_profile_migration_round_trips(migrated) -> None:
    """upgrade → downgrade → upgrade оставляет схему рабочей.

    Откат уносит и таблицу, и расширение vector: чистое окружение после
    повторного upgrade не должно зависеть от ручных шагов.
    """
    engine = migrated
    config = _config()

    with engine.begin() as conn:
        rows = conn.execute(
            text("SELECT count(*) FROM repo_code_chunks")
        ).scalar_one()
    assert rows == 0

    command.downgrade(config, "0002")
    with engine.begin() as conn:
        exists = conn.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = 'repo_code_chunks'"
            )
        ).scalar_one()
    assert exists == 0

    command.upgrade(config, "head")
    with engine.begin() as conn:
        conn.execute(text("SELECT embedding_model FROM repo_code_chunks LIMIT 1"))
