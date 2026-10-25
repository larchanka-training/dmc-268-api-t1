"""Обвязка миграций: строка подключения и тайм-ауты сессии.

URL проходит через alembic.ini, а это файл configparser, поэтому что этот файл
делает со значением по дороге — забота этого модуля. Тайм-ауты ставит онлайн-
режим `alembic/env.py`: миграция, которая ждёт блокировку горячей таблицы,
ставит за собой в очередь все её чтения (#213).
"""

import io
import threading
import time

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

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


def test_offline_sql_has_no_session_timeouts() -> None:
    """Тайм-ауты — свойство сессии деплоя, а не SQL, который отдают на ревью или DBA."""
    buffer = io.StringIO()
    config = Config("alembic.ini", output_buffer=buffer)
    config.set_main_option("sqlalchemy.url", "postgresql+psycopg://u@localhost/d")
    command.upgrade(config, "head", sql=True)
    sql = buffer.getvalue()
    assert "CREATE TABLE alembic_version" in sql
    assert "lock_timeout" not in sql and "statement_timeout" not in sql


@pytest.mark.integration
@requires_db
def test_a_migration_waiting_for_a_lock_gives_up_after_lock_timeout(migrated) -> None:
    """Сценарий #213: чужая транзакция держит блокировку — миграция падает за 5 с, а не ждёт её конца.

    Блокировку держит соседнее соединение 20 с. Без lock_timeout upgrade дождался
    бы её и прошёл — тест упал бы на «не было исключения», а не повис.
    """
    assert TEST_DATABASE_URL is not None
    locked, release = threading.Event(), threading.Event()

    def hold() -> None:
        with migrated.connect() as conn:
            conn.execute(text("LOCK TABLE alembic_version IN ACCESS EXCLUSIVE MODE"))
            locked.set()
            release.wait(20)
            conn.rollback()

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert locked.wait(10), "соседнее соединение не взяло блокировку"
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
        started = time.monotonic()
        with pytest.raises(OperationalError) as caught:
            command.upgrade(config, "head")
        waited = time.monotonic() - started
    finally:
        release.set()
        holder.join()

    # 55P03 lock_not_available — сработал именно lock_timeout, а не statement_timeout (57014).
    assert getattr(caught.value.orig, "sqlstate", None) == "55P03"
    assert 4 < waited < 15, waited
