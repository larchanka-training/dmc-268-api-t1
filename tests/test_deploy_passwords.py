"""Пароли базы и брокера из секретов деплоя (#161): `.github/scripts/deploy/passwords.sh`.

`POSTGRES_PASSWORD` и `RABBITMQ_DEFAULT_PASS` действуют только при
инициализации тома: новый секрет без этого шага дошёл бы до API, воркера и
миграций, но не до базы и брокера. Шаг выставляет пароли изнутри контейнеров
(у postgres — локальный сокет с `trust`, у брокера — `rabbitmqctl`), а сами
контейнеры смену секрета не пересоздают: `ignore_changes = [env]`.

Скрипт гоняется настоящим bash, а то, что он отправляет в контейнер, —
настоящим `sh` с подложными `psql` и `rabbitmqctl`: подложный `docker exec`
запускает переданный скрипт локально, с окружением контейнера. Пароль базы и
брокера «хранится» в файлах каталога состояния: так видно, что поменялось, а
журнал аргументов показывает, что пароль не ушёл ни в один argv.
"""

import os
import re
import subprocess
from pathlib import Path

import yaml

from tests.infra_text import read_infra_text

SCRIPT = Path(".github/scripts/deploy/passwords.sh")
DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")

# Подложный docker: `exec -i <контейнер> sh -c <скрипт>` — локальный sh с
# окружением контейнера. DOCKER_MISSING — контейнер, которого нет (код 1, как
# у docker exec).
FAKE_DOCKER = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "$CALLS"
[ "$1" = exec ] || exit 64
shift
[ "$1" = -i ] || exit 64
shift
name="$1"
shift
if [ "$name" = "${DOCKER_MISSING:-}" ]; then echo "Error response from daemon: No such container: $name" >&2; exit 1; fi
case "$name" in
  dmc268-postgres) export POSTGRES_USER=dmc268 POSTGRES_DB=dmc268 ;;
  dmc268-rabbitmq) export RABBITMQ_DEFAULT_USER=dmc268 ;;
  *) exit 1 ;;
esac
exec "$@"
"""

# Подложный psql. С `-h` — вход по сети (scram): пароль из PGPASSWORD
# сверяется с «хранимым». Без `-h` — локальный сокет (trust): SQL из stdin
# пишется в журнал, а ALTER ROLE ставит пароль из той переменной окружения,
# которую назвал `\\getenv` (как настоящий psql). PSQL_ALTER_NOOP — ALTER
# проходит, но пароль не меняется.
FAKE_PSQL = """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >> "$CALLS"
case " $* " in
  *" -h "*) [ "${PGPASSWORD:-}" = "$(cat "$STATE/pg")" ] || { echo "password authentication failed" >&2; exit 2; }; exit 0 ;;
esac
sql="$(cat)"
printf '%s\\n' "$sql" > "$STATE/pg.sql"
# Переменная psql в PASSWORD :'имя' → переменная окружения из `\\getenv имя ENV`.
name="$(printf '%s\\n' "$sql" | sed -n "s/^ALTER ROLE .* PASSWORD :'\\([a-z_]*\\)';$/\\1/p")"
var="$(printf '%s\\n' "$sql" | sed -n "s/^.getenv $name \\([A-Z_]*\\)$/\\1/p")"
if [ -n "$name" ] && [ -n "$var" ] && [ -z "${PSQL_ALTER_NOOP:-}" ]; then printf '%s' "${!var}" > "$STATE/pg"; fi
"""

# Подложный rabbitmqctl: пароль — первой строкой stdin, как у настоящего.
# AWAIT_FAILS — сколько раз await_startup ответит «узел не готов».
FAKE_RABBITMQCTL = """#!/usr/bin/env bash
printf 'rabbitmqctl %s\\n' "$*" >> "$CALLS"
for a in "$@"; do case "$a" in -*) ;; *) cmd="$a"; break ;; esac; done
case "$cmd" in
  await_startup)
    n=$(( $(cat "$STATE/await" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$STATE/await"
    [ "$n" -gt "${AWAIT_FAILS:-0}" ] ;;
  authenticate_user) IFS= read -r pw; [ "$pw" = "$(cat "$STATE/rabbit")" ] ;;
  change_password) IFS= read -r pw; printf '%s' "$pw" > "$STATE/rabbit" ;;
  *) exit 64 ;;
esac
"""

# Без пауз: подложный await_startup отвечает сразу.
FAKE_SLEEP = "#!/usr/bin/env bash\nexit 0\n"

OLD_PG, NEW_PG = "old-pg-pass", "n3w pg'pa$$:\\word"
OLD_RABBIT, NEW_RABBIT = "old-rabbit", "n3w r@bbit \"pass\""


def run(
    tmp_path: Path, *, pg: str = OLD_PG, rabbit: str = OLD_RABBIT, **env: str
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """passwords.sh с подложными docker, psql и rabbitmqctl; вернуть результат и каталог состояния."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (
        ("docker", FAKE_DOCKER), ("psql", FAKE_PSQL), ("rabbitmqctl", FAKE_RABBITMQCTL), ("sleep", FAKE_SLEEP),
    ):
        (bin_dir / name).write_text(body)
        (bin_dir / name).chmod(0o755)
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    (state / "pg").write_text(pg)
    (state / "rabbit").write_text(rabbit)
    result = subprocess.run(
        ["bash", str(SCRIPT.resolve())],
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "CALLS": str(tmp_path / "calls"),
            "STATE": str(state),
            "DOCKER_HOST": "ssh://stand",
            "PG_PASSWORD": NEW_PG,
            "RABBITMQ_PASSWORD": NEW_RABBIT,
            **env,
        },
    )
    return result, state


def calls(tmp_path: Path) -> str:
    path = tmp_path / "calls"
    return path.read_text() if path.exists() else ""


def test_new_secrets_reach_the_database_and_the_broker(tmp_path: Path) -> None:
    result, state = run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "pg").read_text() == NEW_PG
    assert (state / "rabbit").read_text() == NEW_RABBIT
    assert "postgres: пароль роли dmc268 сменён" in result.stdout
    assert "rabbitmq: пароль пользователя dmc268 сменён" in result.stdout


def test_passwords_never_reach_an_argument_or_the_log(tmp_path: Path) -> None:
    """Аргументы видны в `ps` на сервере и в логе ssh, вывод шага — всем, кто читает репозиторий."""
    result, _ = run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    log = calls(tmp_path)
    assert "docker exec -i dmc268-postgres" in log
    assert "docker exec -i dmc268-rabbitmq" in log
    for secret in (NEW_PG, NEW_RABBIT, OLD_PG, OLD_RABBIT):
        assert secret not in log
        assert secret not in result.stdout + result.stderr


def test_unchanged_passwords_are_left_alone(tmp_path: Path) -> None:
    """Обычный деплой: секреты не менялись — ни ALTER ROLE, ни change_password."""
    result, state = run(tmp_path, pg=NEW_PG, rabbit=NEW_RABBIT)

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (state / "pg.sql").exists()
    assert not [c for c in calls(tmp_path).splitlines() if c.startswith("rabbitmqctl") and "change_password" in c]
    assert "postgres: пароль роли dmc268 совпадает с секретом" in result.stdout
    assert "rabbitmq: пароль пользователя dmc268 совпадает с секретом" in result.stdout


def test_alter_role_keeps_the_password_out_of_server_logs(tmp_path: Path) -> None:
    """Упавший ALTER ROLE PostgreSQL пишет в лог строкой STATEMENT, psql — строкой LINE (проверено на pg18)."""
    result, state = run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    sql = (state / "pg.sql").read_text()
    assert "\\set VERBOSITY terse" in sql
    assert "SET log_min_error_statement = panic;" in sql
    assert "SET log_statement = none;" in sql
    assert re.search(r"^\\getenv \w+ NEW_PASSWORD$", sql, re.MULTILINE)
    assert re.search(r"^ALTER ROLE :\"\w+\" PASSWORD :'\w+';$", sql, re.MULTILINE)
    # Вход по сети проверяется уже новым паролем — им придут API и миграции.
    assert "-h dmc268-postgres" in calls(tmp_path)


def test_a_password_that_did_not_take_stops_the_deploy(tmp_path: Path) -> None:
    result, _ = run(tmp_path, PSQL_ALTER_NOOP="1")

    assert result.returncode != 0
    assert "::error::" in result.stdout
    # До брокера не дошло: деплой и так остановлен.
    assert "dmc268-rabbitmq" not in calls(tmp_path)


def test_a_missing_container_stops_the_deploy(tmp_path: Path) -> None:
    result, state = run(tmp_path, DOCKER_MISSING="dmc268-rabbitmq")

    assert result.returncode != 0
    assert (state / "pg").read_text() == NEW_PG


def test_the_broker_is_awaited_on_a_clean_server(tmp_path: Path) -> None:
    """На чистом сервере брокер только что создан, а `wait` у его контейнера нет."""
    result, state = run(tmp_path, AWAIT_FAILS="3")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "rabbit").read_text() == NEW_RABBIT


def test_a_broker_that_never_starts_stops_the_deploy(tmp_path: Path) -> None:
    result, state = run(tmp_path, AWAIT_FAILS="1000")

    assert result.returncode != 0
    assert (state / "rabbit").read_text() == OLD_RABBIT


def test_an_empty_or_multiline_secret_is_rejected_before_the_server(tmp_path: Path) -> None:
    for name, value in (("PG_PASSWORD", ""), ("RABBITMQ_PASSWORD", "a\nb")):
        (tmp_path / "calls").unlink(missing_ok=True)
        result, _ = run(tmp_path, **{name: value})

        assert result.returncode != 0, name
        assert f"::error::{name}" in result.stdout
        assert calls(tmp_path) == "", name


# --- конфигурация и раскладка job'а ------------------------------------------

# Переменные образов postgres и rabbitmq, которые действуют только на пустом
# томе: при живом томе их смена ничего не меняет, поэтому контейнер её не видит.
INIT_ONLY_ENV = {
    "postgres": {"POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB"},
    "rabbitmq": {"RABBITMQ_DEFAULT_USER", "RABBITMQ_DEFAULT_PASS"},
}


def container_block(name: str) -> str:
    match = re.search(
        rf'^resource\s+"docker_container"\s+"{name}"\s*\{{\n(.*?)^\}}', read_infra_text(), re.MULTILINE | re.DOTALL
    )
    assert match, name
    return match.group(1)


def test_a_new_secret_does_not_recreate_the_database_or_the_broker() -> None:
    """env у базы и брокера — только переменные инициализации тома, и их правку план не видит.

    Переменная, которая действует при каждом старте, сюда не годится: под
    `ignore_changes` её правка не дошла бы до контейнера молча.
    """
    for name, allowed in INIT_ONLY_ENV.items():
        body = container_block(name)
        env = re.search(r"^  env = \[\n(.*?)^  \]", body, re.MULTILINE | re.DOTALL)
        assert env, name
        keys = set(re.findall(r'^\s*"(\w+)=', env.group(1), re.MULTILINE))
        assert keys == allowed, (name, keys ^ allowed)
        assert re.search(r"^  lifecycle \{[^}]*^\s*ignore_changes = \[env\]$", body, re.MULTILINE), name


def deploy_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    return list(workflow["jobs"]["deploy"]["steps"])


def test_passwords_are_set_after_the_checked_plan_and_before_migrations() -> None:
    """После плана: отклонённый план пароль не трогает. До миграций: они входят уже новым."""
    ids = [s.get("id") for s in deploy_steps()]
    assert ids.index("plan") + 1 == ids.index("passwords") == ids.index("migrate") - 1

    step = next(s for s in deploy_steps() if s.get("id") == "passwords")
    assert str(step["run"]).strip().splitlines()[-1] == f".github/scripts/deploy/{SCRIPT.name}"
    env = step["env"]
    assert isinstance(env, dict)
    assert env["DOCKER_HOST"] == "ssh://stand"
    assert env["PG_PASSWORD"] == "${{ secrets.PG_PASSWORD_DMC268_T1 }}"
    assert env["RABBITMQ_PASSWORD"] == "${{ secrets.RABBITMQ_PASSWORD_DMC268_T1 }}"
    # Брокер на чистом сервере ждём до двух минут; зависший ssh не держит job.
    assert step["timeout-minutes"] == 5
