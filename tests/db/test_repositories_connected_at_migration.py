"""Ревизия 0014: `repositories.connected_at` — записи до неё включены с момента регистрации."""

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from alembic import command

from ..conftest import TEST_DATABASE_URL, requires_db
from .test_constraints import NOW, make_repo

pytestmark = [pytest.mark.integration, requires_db]


def _config() -> Config:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


def _columns(engine) -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns("repositories")}


def test_connected_at_is_filled_from_created_at_and_dropped_on_downgrade(clean_db) -> None:
    """До ревизии выключения не было: каждая запись включена, время включения — время регистрации."""
    with Session(clean_db) as session:
        repo_id = make_repo(session).id
        session.execute(text("UPDATE repositories SET created_at = :at"), {"at": NOW})
        session.commit()
    config = _config()
    try:
        command.downgrade(config, "0013")
        assert "connected_at" not in _columns(clean_db)
    finally:
        command.upgrade(config, "head")
    with clean_db.connect() as conn:
        rows = conn.execute(text("SELECT id, connected_at FROM repositories")).all()
    assert [tuple(row) for row in rows] == [(repo_id, NOW)]


def test_an_insert_without_connected_at_creates_a_connected_repository(clean_db) -> None:
    """Старый код в окне деплоя и ручная вставка из README колонку не знают: запись — включённая."""
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO repositories (id, provider, provider_id, full_name, default_branch, auto_review_enabled)"
                " VALUES (gen_random_uuid(), 'github', '1', 'acme/api', 'main', true)"
            )
        )
    with clean_db.connect() as conn:
        connected_at = conn.execute(text("SELECT connected_at FROM repositories")).scalar_one()
    assert connected_at is not None
