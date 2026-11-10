"""Ревизия 0023 откатывается начисто и накатывается снова."""

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


def _columns(engine) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns("findings")}


def _verdict_type_exists(engine) -> bool:
    with engine.connect() as conn:
        return conn.execute(text("SELECT 1 FROM pg_type WHERE typname = 'verifier_verdict'")).first() is not None


def test_the_verdict_revision_downgrades_without_leftovers_and_upgrades_again(migrated) -> None:
    config = _config()
    assert {"verifier_verdict", "verifier_reason"} <= _columns(migrated)
    try:
        command.downgrade(config, "0022")
        assert not {"verifier_verdict", "verifier_reason"} & _columns(migrated)
        assert not _verdict_type_exists(migrated)
    finally:
        # Остальные тесты сессии ждут базу на head.
        command.upgrade(config, "head")
    assert {"verifier_verdict", "verifier_reason"} <= _columns(migrated)
    assert _verdict_type_exists(migrated)


def test_the_verdict_columns_are_nullable_so_old_findings_need_no_backfill(migrated) -> None:
    columns = {c["name"]: c["nullable"] for c in inspect(migrated).get_columns("findings")}
    assert columns["verifier_verdict"] and columns["verifier_reason"]
