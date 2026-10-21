"""Ревизия 0007 откатывается начисто и накатывается снова."""

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text

from alembic import command

from ..conftest import TEST_DATABASE_URL, requires_db

pytestmark = [pytest.mark.integration, requires_db]


def _config() -> Config:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


def _enum_types(engine) -> set[str]:
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT typname FROM pg_type"
                " WHERE typname IN ('review_run_stage', 'review_run_step_status')"
            )
        )
        return {row[0] for row in rows}


def test_the_steps_revision_downgrades_without_leftovers_and_upgrades_again(
    migrated,
) -> None:
    config = _config()
    try:
        command.downgrade(config, "0006")
        assert "review_run_steps" not in inspect(migrated).get_table_names()
        # Типы, оставленные откатом, уронили бы следующий upgrade.
        assert _enum_types(migrated) == set()
    finally:
        # Остальные тесты сессии ждут базу на head.
        command.upgrade(config, "head")
    assert "review_run_steps" in inspect(migrated).get_table_names()
    assert _enum_types(migrated) == {"review_run_stage", "review_run_step_status"}
