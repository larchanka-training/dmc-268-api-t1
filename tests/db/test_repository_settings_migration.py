"""Ревизия 0015: настройки ревью в `repositories` — умолчания для старых записей и окна деплоя."""

from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command

from ..conftest import TEST_DATABASE_URL, requires_db

pytestmark = [pytest.mark.integration, requires_db]

# Вставка старого кода: про настройки он не знает, `auto_review_enabled` пишет сам.
_OLD_INSERT = (
    "INSERT INTO repositories (id, provider, provider_id, full_name, default_branch, auto_review_enabled)"
    " VALUES (gen_random_uuid(), 'github', :provider_id, 'acme/api', 'main', :enabled)"
)


def _config() -> Config:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


def _columns(engine) -> dict[str, dict[str, Any]]:
    return {column["name"]: column for column in inspect(engine).get_columns("repositories")}


def _settings(engine) -> list[tuple[Any, ...]]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT provider_id, auto_review_enabled, branch_filter, min_severity::text"
                " FROM repositories ORDER BY provider_id"
            )
        ).all()
    return [tuple(row) for row in rows]


def test_upgrade_gives_existing_repositories_default_settings(clean_db) -> None:
    """Записи до ревизии получают пустой фильтр и порог `critical`; `auto_review_enabled` не меняется."""
    config = _config()
    try:
        command.downgrade(config, "0014")
        with clean_db.begin() as conn:
            conn.execute(text(_OLD_INSERT), {"provider_id": "1", "enabled": True})
            conn.execute(text(_OLD_INSERT), {"provider_id": "2", "enabled": False})
    finally:
        command.upgrade(config, "head")
    assert _settings(clean_db) == [("1", True, [], "critical"), ("2", False, [], "critical")]


def test_an_insert_without_settings_gets_the_contract_defaults(clean_db) -> None:
    """Вставка без настроек (старый API в окне деплоя, ручная) — `true`, `{}`, `critical`."""
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO repositories (id, provider, provider_id, full_name, default_branch)"
                " VALUES (gen_random_uuid(), 'github', '1', 'acme/api', 'main')"
            )
        )
    assert _settings(clean_db) == [("1", True, [], "critical")]


def test_downgrade_drops_the_columns_and_the_default_keeping_the_data(clean_db) -> None:
    """Откат: колонок настроек нет, у `auto_review_enabled` нет умолчания, записи целы."""
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO repositories (id, provider, provider_id, full_name, default_branch,"
                " auto_review_enabled, branch_filter, min_severity)"
                " VALUES (gen_random_uuid(), 'github', '1', 'acme/api', 'main', false, '{main}', 'high')"
            )
        )
    config = _config()
    try:
        command.downgrade(config, "0014")
        columns = _columns(clean_db)
        assert "branch_filter" not in columns
        assert "min_severity" not in columns
        assert columns["auto_review_enabled"]["default"] is None
        with clean_db.connect() as conn:
            rows = conn.execute(
                text("SELECT provider_id, full_name, auto_review_enabled FROM repositories")
            ).all()
        assert [tuple(row) for row in rows] == [("1", "acme/api", False)]
        # Тип важности остаётся: им пользуются находки.
        with clean_db.connect() as conn:
            assert conn.execute(text("SELECT to_regtype('finding_severity')")).scalar_one() is not None
    finally:
        command.upgrade(config, "head")
