"""Обвязка миграций: строка подключения и тайм-ауты сессии.

URL проходит через alembic.ini, а это файл configparser, поэтому что этот файл
делает со значением по дороге — забота этого модуля. Тайм-ауты ставит онлайн-
режим `alembic/env.py`: миграция, которая ждёт блокировку горячей таблицы,
ставит за собой в очередь все её чтения (#213).
"""

import io
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import OperationalError

from alembic import command
from scripts import deploy_report

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


@pytest.mark.integration
@requires_db
def test_the_migrations_session_carries_both_timeouts(migrated) -> None:
    """Ревью #220: `SHOW` в той самой сессии, которую настраивает env.py.

    Сессию видно только изнутри: alembic открывает своё соединение и закрывает
    его (NullPool). Слушатель на чтении `alembic_version` — уже после `SET` —
    спрашивает настройки в том же соединении.
    """
    assert TEST_DATABASE_URL is not None
    seen: dict[str, str] = {}

    def show(conn, cursor, statement, parameters, context, executemany) -> None:
        if "alembic_version" in statement and not seen:
            seen["lock_timeout"] = cursor.connection.execute("SHOW lock_timeout").fetchone()[0]
            seen["statement_timeout"] = cursor.connection.execute("SHOW statement_timeout").fetchone()[0]

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    event.listen(Engine, "after_cursor_execute", show)
    try:
        command.upgrade(config, "head")
    finally:
        event.remove(Engine, "after_cursor_execute", show)

    assert seen == {"lock_timeout": "5s", "statement_timeout": "2min"}


def _alembic_cli(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """`alembic` отдельным процессом, как на стенде: свой `env.py`, свои логи в stderr.

    База — через копию alembic.ini с `sqlalchemy.url`: `env.py` берёт опцию,
    выставленную до него, и Settings (весь env стенда) не нужны.
    """
    assert TEST_DATABASE_URL is not None
    ini = tmp_path / "alembic.ini"
    ini.write_text(
        Path("alembic.ini").read_text().replace(
            "[alembic]\n", f"[alembic]\nsqlalchemy.url = {TEST_DATABASE_URL.replace('%', '%%')}\n", 1
        )
    )
    env = {k: v for k, v in os.environ.items() if not k.startswith("OTEL_")}
    return subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ini), *args],
        capture_output=True, text=True, check=True, env=env, timeout=120,
    )


@pytest.mark.integration
@requires_db
def test_the_deploy_report_reads_real_alembic_output(migrated, tmp_path: Path) -> None:
    """Ревью #130, #232: stdout `alembic current` — только ревизия, JSON-логи — в stderr.

    `migrate.sh` кладёт stdout в migrate-current.log, а повтор по блокировке
    и summary берут ревизию оттуда. Разбор сверяется с настоящим выводом, а
    не с подложным docker. Заодно — `Running upgrade` в JSON-логе upgrade:
    по нему summary считает применённые ревизии.
    """
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))

    script = ScriptDirectory.from_config(config)
    head = script.get_current_head()
    assert head is not None
    previous = script.get_revision(head).down_revision
    assert isinstance(previous, str)

    current = _alembic_cli(tmp_path, "current")
    # В stdout — ни одной строки лога: его читают скрипты деплоя.
    assert current.stdout.split() == [head, "(head)"], current.stdout
    assert deploy_report.current_revision(current.stdout) == head
    # Логи никуда не делись — они JSON в stderr.
    assert json.loads(current.stderr.splitlines()[0])["service.name"] == "dmc-268-migrate"

    command.downgrade(config, "-1")
    try:
        upgrade = _alembic_cli(tmp_path, "upgrade", "head")
    finally:
        # Остальные тесты сессии ждут базу на head.
        command.upgrade(config, "head")
    log = upgrade.stdout + upgrade.stderr
    assert deploy_report.migrations(log) == {"applied": 1, "from": previous, "to": head}
