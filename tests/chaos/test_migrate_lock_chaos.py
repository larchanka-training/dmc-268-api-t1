"""Миграции деплоя за чужой блокировкой: цикл повторов `migrate.sh` целиком (ревью #220).

Юнит-тесты `tests/test_deploy_report.py` гоняют `migrate.sh` с подложным docker и
готовыми логами alembic, а `tests/db/test_alembic_env.py` — `lock_timeout` на
настоящей базе. Здесь связка целиком: настоящий `migrate.sh` (цикл, пауза,
`set +e`/`set -e`, `$?` после `tee` под `pipefail`), настоящий alembic с
`alembic/env.py` и `scripts/deploy_report.py migrate-retry` против PostgreSQL, где
соседнее соединение держит `ACCESS EXCLUSIVE` на таблице следующей ревизии.

Подложены только `tofu` (окружение миграций) и `docker`: `docker run … alembic …`
запускает alembic этого окружения с переменными из `--env-file`, как контейнер
`dmc268-migrations` на стенде.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

from alembic import command

from ..conftest import TEST_DATABASE_URL, requires_db

pytestmark = [pytest.mark.integration, pytest.mark.chaos, requires_db]

MIGRATE_SCRIPT = Path(".github/scripts/deploy/migrate.sh").resolve()

# Ревизия, которую упирают в блокировку: 0012 первым делом меняет `findings`.
FROM_REVISION, LOCKED_TABLE, ADDED_COLUMN = "0011", "findings", "covered_by_finding_id"

FAKE_TOFU = """#!/usr/bin/env bash
case "$*" in
  *"output -raw migrate_env") printf '%s\\n' "DATABASE_URL=$MIGRATE_DATABASE_URL" \\
    "RABBITMQ_URL=amqp://guest:guest@localhost:5672/%2F" "GITHUB_WEBHOOK_SECRET=chaos" ;;
  *) exit 3 ;;
esac
"""

# `run`: флаги migrations_run из migrate.sh отбрасываются, переменные из
# --env-file экспортируются, после образа — команда alembic. `pull` и `rm` — no-op.
FAKE_DOCKER = """#!/usr/bin/env bash
case "$1" in
  run)
    shift
    while [ $# -gt 0 ]; do
      case "$1" in
        --env-file) while IFS= read -r line; do export "$line"; done < "$2"; shift 2 ;;
        --rm|--read-only|--init) shift ;;
        --*) shift 2 ;;
        *) break ;;
      esac
    done
    shift
    [ "$1" = alembic ] || exit 125
    shift
    exec "$ALEMBIC" "$@" ;;
  pull|rm) exit 0 ;;
  *) exit 125 ;;
esac
"""

# В macOS нет coreutils `timeout`; `ask_current` в migrate.sh зовёт его. В CI
# (ubuntu) он есть и подмена не ставится.
FAKE_TIMEOUT = """#!/usr/bin/env bash
shift
exec "$@"
"""


def alembic_config() -> Config:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


@pytest.fixture
def behind_head(migrated: Engine) -> Iterator[Engine]:
    """База на ревизии до 0012; после теста — снова на head для остальных тестов сессии."""
    command.downgrade(alembic_config(), FROM_REVISION)
    yield migrated
    command.upgrade(alembic_config(), "head")


def run_migrate(tmp_path: Path) -> tuple[subprocess.CompletedProcess[str], Path]:
    """migrate.sh из корня репозитория с подложными tofu и docker; результат и каталог отчёта."""
    assert TEST_DATABASE_URL is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fakes = {"tofu": FAKE_TOFU, "docker": FAKE_DOCKER}
    if shutil.which("timeout") is None:
        fakes["timeout"] = FAKE_TIMEOUT
    for name, body in fakes.items():
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    report = tmp_path / "report"
    result = subprocess.run(
        ["bash", str(MIGRATE_SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "HOME": os.environ.get("HOME", str(tmp_path)),
            "API_IMAGE": "ghcr.io/o/r:t@sha256:" + "0" * 64,
            "DOCKER_HOST": "ssh://stand",
            "REPORT_DIR": str(report),
            "MIGRATE_RETRY_PAUSE": "1",
            "MIGRATE_DATABASE_URL": TEST_DATABASE_URL,
            "ALEMBIC": str(Path(sys.executable).parent / "alembic"),
        },
    )
    return result, report


def hold_lock(engine: Engine, release: Callable[[], bool]) -> tuple[threading.Thread, threading.Event]:
    """Соседнее соединение держит ACCESS EXCLUSIVE на таблице 0012, пока `release()` ложно."""
    locked = threading.Event()

    def hold() -> None:
        with engine.connect() as conn:
            conn.execute(text(f"LOCK TABLE {LOCKED_TABLE} IN ACCESS EXCLUSIVE MODE"))
            locked.set()
            deadline = time.monotonic() + 90
            while not release() and time.monotonic() < deadline:
                time.sleep(0.05)
            conn.rollback()

    holder = threading.Thread(target=hold)
    holder.start()
    assert locked.wait(10), "соседнее соединение не взяло блокировку"
    return holder, locked


def revision(engine: Engine) -> str:
    with engine.connect() as conn:
        return str(conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one())


def has_added_column(engine: Engine) -> bool:
    return ADDED_COLUMN in {c["name"] for c in inspect(engine).get_columns(LOCKED_TABLE)}


def test_a_lock_released_before_the_retry_lets_the_second_attempt_through(
    behind_head: Engine, tmp_path: Path
) -> None:
    """Длинная транзакция закончилась за паузу: вторая попытка проходит, шаг зелёный.

    Блокировку отпускаем, когда migrate.sh начал вторую попытку (`migrate-attempts`
    = 2), а не по таймеру: первая попытка гарантированно упирается в lock_timeout.
    """
    attempts = tmp_path / "report" / "migrate-attempts"
    holder, _ = hold_lock(
        behind_head, lambda: attempts.exists() and attempts.read_text().strip() == "2"
    )
    try:
        result, report = run_migrate(tmp_path)
    finally:
        holder.join()

    assert result.returncode == 0, result.stdout + result.stderr
    assert attempts.read_text().strip() == "2"
    assert "::warning::миграции не дождались блокировки" in result.stdout
    # Решение о повторе — по ответу базы после сбоя: она осталась на исходной ревизии.
    assert (report / "migrate-current.log").read_text().split()[0] == FROM_REVISION
    # В отчёте — лог удачной попытки.
    log = (report / "migrate.log").read_text()
    assert f"Running upgrade {FROM_REVISION} -> 0012" in log
    assert "LockNotAvailable" not in log
    assert revision(behind_head) != FROM_REVISION
    assert has_added_column(behind_head)


def test_a_lock_outliving_three_attempts_fails_the_step_with_the_upgrade_exit_code(
    behind_head: Engine, tmp_path: Path
) -> None:
    """Блокировка дольше всех попыток: шаг красный с кодом upgrade, а не 0 от `tee` или `ask_current`."""
    done = threading.Event()
    holder, _ = hold_lock(behind_head, done.is_set)
    try:
        result, report = run_migrate(tmp_path)
    finally:
        done.set()
        holder.join()

    assert result.returncode == 1, result.stdout + result.stderr
    assert (report / "migrate-attempts").read_text().strip() == "3"
    assert result.stdout.count("::warning::миграции не дождались блокировки") == 2
    assert "LockNotAvailable" in (report / "migrate.log").read_text()
    assert (report / "migrate-current.log").read_text().split()[0] == FROM_REVISION
    assert not (report / "migrate-current.error").exists()
    # Каждая попытка откатилась целиком: база там же, где была.
    assert revision(behind_head) == FROM_REVISION
    assert not has_added_column(behind_head)
