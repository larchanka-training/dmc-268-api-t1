"""Ревизия 0016 откатывается начисто и накатывается снова."""

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
                " WHERE typname IN ('simulation_status', 'simulation_source_kind', 'review_verification')"
            )
        )
        return {row[0] for row in rows}


def test_the_simulations_revision_downgrades_without_leftovers_and_upgrades_again(migrated) -> None:
    config = _config()
    try:
        command.downgrade(config, "0015")
        assert "simulations" not in inspect(migrated).get_table_names()
        # Свои типы ушли, общий с прогонами `review_verification` остался.
        assert _enum_types(migrated) == {"review_verification"}
    finally:
        # Остальные тесты сессии ждут базу на head.
        command.upgrade(config, "head")
    assert "simulations" in inspect(migrated).get_table_names()
    assert _enum_types(migrated) == {"simulation_status", "simulation_source_kind", "review_verification"}


def test_the_simulations_revision_indexes_launches_and_expiry(migrated) -> None:
    indexes = {i["name"]: (i["column_names"], i["unique"]) for i in inspect(migrated).get_indexes("simulations")}
    assert indexes == {
        "ix_simulations_expires_at": (["expires_at"], False),
        "ix_simulations_user_created_at": (["user_id", "created_at"], False),
        "uq_simulations_one_active_per_user": (["user_id"], True),
    }
