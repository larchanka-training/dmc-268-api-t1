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

    В чистой базе откат уносит и таблицу, и расширение vector: повторный
    upgrade не должен зависеть от ручных шагов.
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
        extension = conn.execute(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
    assert exists == 0
    assert extension == 0

    command.upgrade(config, "head")
    with engine.begin() as conn:
        conn.execute(text("SELECT embedding_model FROM repo_code_chunks LIMIT 1"))


def test_downgrade_keeps_a_foreign_vector_column(migrated) -> None:
    """Расширение общее: чужая vector-колонка переживает откат нашей миграции."""
    engine = migrated
    config = _config()

    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE keep_me (v vector(3) NOT NULL)"))
    command.downgrade(config, "0002")
    with engine.begin() as conn:
        table = conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_name = 'keep_me'")
        ).scalar_one()
        extension = conn.execute(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
        ).scalar_one()
    assert table == 1
    assert extension == 1

    # возвращаем базу в head и убираем за собой
    command.upgrade(config, "head")
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE keep_me"))
