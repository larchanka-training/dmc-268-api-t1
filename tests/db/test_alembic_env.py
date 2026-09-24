"""Как миграции получают строку подключения.

URL проходит через alembic.ini, а это файл configparser, поэтому что этот файл
делает со значением по дороге — забота этого модуля.
"""

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url

from alembic import command

from ..conftest import TEST_DATABASE_URL, requires_db

# `p%40ss` — это percent-encoded `p@ss`, обычный способ поставить `@` в DSN.
# Он же, с точки зрения configparser, начало интерполяции.
HOSTILE_PASSWORD = "p@ss"
HOSTILE_ROLE = "pct_test"


def test_a_percent_encoded_password_survives_the_ini() -> None:
    url = "postgresql+psycopg://dmc:p%40ss@localhost:5432/dmc268"
    config = Config()
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    assert config.get_main_option("sqlalchemy.url") == url


def test_an_unescaped_percent_is_what_used_to_break() -> None:
    """configparser отвергает значение при записи, а не при чтении."""
    config = Config()
    with pytest.raises(ValueError, match="invalid interpolation syntax"):
        config.set_main_option(
            "sqlalchemy.url", "postgresql+psycopg://dmc:p%40ss@localhost:5432/dmc268"
        )


@pytest.fixture
def percent_dsn(migrated):
    """DSN с паролем, который приходится percent-encode'ить, и роль под него."""
    with migrated.begin() as conn:
        conn.execute(text(f"DROP ROLE IF EXISTS {HOSTILE_ROLE}"))
        conn.execute(
            text(
                f"CREATE ROLE {HOSTILE_ROLE} LOGIN SUPERUSER "
                f"PASSWORD '{HOSTILE_PASSWORD}'"
            )
        )
    # `migrated` (собственная зависимость этой фикстуры) уже пропускает сессию
    # тестов, если переменная не задана, так что здесь она точно задана.
    assert TEST_DATABASE_URL is not None
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
