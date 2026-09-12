"""How migrations get their connection string.

The URL passes through alembic.ini, which is a configparser file, so what that
file does to the value on the way through is this module's business.
"""

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from alembic import command

from ..conftest import TEST_DATABASE_URL, requires_db

# `p%40ss` is `p@ss` percent-encoded, which is the ordinary way to put an `@`
# in a DSN. It is also, to configparser, the start of an interpolation.
HOSTILE_PASSWORD = "p@ss"
HOSTILE_ROLE = "pct_test"


def test_a_percent_encoded_password_survives_the_ini() -> None:
    url = "postgresql+psycopg://dmc:p%40ss@localhost:5432/dmc268"
    config = Config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    assert config.get_main_option("sqlalchemy.url") == url


def test_an_unescaped_percent_is_what_used_to_break() -> None:
    """configparser rejects the value as it is written, not when it is read."""
    config = Config()
    with pytest.raises(ValueError, match="invalid interpolation syntax"):
        config.set_main_option(
            "sqlalchemy.url", "postgresql+psycopg://dmc:p%40ss@localhost:5432/dmc268"
        )


@pytest.fixture
def percent_dsn(migrated):
    """A DSN whose password has to be percent-encoded, and a role to match."""
    with migrated.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {HOSTILE_ROLE}"))
        conn.execute(
            text(
                f"CREATE ROLE {HOSTILE_ROLE} LOGIN SUPERUSER "
                f"PASSWORD '{HOSTILE_PASSWORD}'"
            )
        )
    url = make_url(TEST_DATABASE_URL).set(
        username=HOSTILE_ROLE, password=HOSTILE_PASSWORD
    )
    yield url.render_as_string(hide_password=False)
    with migrated.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {HOSTILE_ROLE}"))


@pytest.mark.integration
@requires_db
def test_migrations_run_against_a_percent_encoded_dsn(percent_dsn) -> None:
    assert "p%40ss" in percent_dsn
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", percent_dsn.replace("%", "%%"))
    command.upgrade(config, "head")
