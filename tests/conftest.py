"""Общие фикстуры.

Интеграционным тестам нужен живой PostgreSQL. Они помечены и пропускаются,
когда TEST_DATABASE_URL не задан, поэтому обычный `pytest` остаётся зелёным и
на машине без базы. Так задумано: всё, что ниже адаптеров, — чистое.

Переменная намеренно называется не DATABASE_URL, и отката к ней нет.
`migrated` удаляет схему, а DATABASE_URL — то, что README велит
экспортировать для uvicorn и alembic. Одна переменная на двоих означала бы,
что обычная рабочая консоль в одном `pytest` от пустой базы разработки.
"""

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.db import models  # noqa: F401  (регистрирует таблицы)
from app.infrastructure.db.base import Base

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="needs a live PostgreSQL; set TEST_DATABASE_URL",
)


@pytest.fixture(scope="session")
def engine():
    if not TEST_DATABASE_URL:
        pytest.skip("needs a live PostgreSQL; set TEST_DATABASE_URL")
    return create_engine(TEST_DATABASE_URL, future=True)


@pytest.fixture(scope="session")
def migrated(engine):
    """База на head, пересобирается один раз за сессию."""
    from alembic.config import Config

    from alembic import command

    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    # `engine` (собственная зависимость этой фикстуры) уже пропускает сессию
    # тестов, когда переменная не задана, так что к этой строке она задана.
    assert TEST_DATABASE_URL is not None

    config = Config("alembic.ini")
    # Явно направляем alembic на тестовую базу. env.py уважает опцию, которую
    # уже выставил вызывающий, поэтому миграция не уйдёт в ту базу, что
    # окажется в DATABASE_URL.
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    command.upgrade(config, "head")
    return engine


@pytest.fixture
def clean_db(migrated):
    """Каждый тест стартует с пустой базы.

    Сессионная фикстура откатом изолирует тест от его собственных записей, но
    не от того, что закоммитил другой тест. Очистка таблиц в начале и делает
    набор независимым от порядка.
    """
    tables = ", ".join(sorted(Base.metadata.tables))
    with migrated.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} CASCADE"))
    return migrated


@pytest.fixture
def session(clean_db) -> Iterator[Session]:
    """Сессия, откатывается после каждого теста, чтобы тесты не видели друг друга."""
    connection = clean_db.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()
