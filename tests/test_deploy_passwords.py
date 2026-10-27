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
# сверяется с «хранимым». Без `-h` — локальный сокет (trust): stdin пишется в
# журнал, а `\\password <роль>` и две строки за ним, как у настоящего psql без
# tty, ставят пароль, если строки совпали. PSQL_ALTER_NOOP — команда проходит,
# но пароль не меняется.
FAKE_PSQL = """#!/usr/bin/env bash
printf 'psql %s\\n' "$*" >> "$CALLS"
case " $* " in
  *" -h "*) [ "${PGPASSWORD:-}" = "$(cat "$STATE/pg")" ] || { echo "password authentication failed" >&2; exit 2; }; exit 0 ;;
esac
IFS= read -r command; IFS= read -r first; IFS= read -r second
printf '%s\\n%s\\n%s\\n' "$command" "$first" "$second" > "$STATE/pg.stdin"
[ "$command" = "\\\\password $POSTGRES_USER" ] || exit 3
[ "$first" = "$second" ] || exit 3
[ -n "${PSQL_ALTER_NOOP:-}" ] || printf '%s' "$first" > "$STATE/pg"
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

# Пароли со всеми классами печатного ASCII: urlencode кодирует их в %XX, и
# SQLAlchemy и pika декодируют обратно верно (проверено end-to-end на dind).
OLD_PG, NEW_PG = "old-pg-pass", "x'y\"z\\$"
OLD_RABBIT, NEW_RABBIT = "old-rabbit", "a+b/c=d"

# Допустимые пароли (0x21–0x7E): последний — `openssl rand -base64 24`.
ACCEPTED = ("a+b/c=d", "p@ss:w%rd", "x'y\"z\\$", "q1Zr+8m/0KxV3aYw9bT2nC6dE4fH7jLs")


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
            "REPORT_DIR": str(tmp_path / "report"),
            "PG_PASSWORD": NEW_PG,
            "RABBITMQ_PASSWORD": NEW_RABBIT,
            **env,
        },
    )
    return result, state


def calls(tmp_path: Path) -> str:
    path = tmp_path / "calls"
    return path.read_text() if path.exists() else ""


def changed(tmp_path: Path) -> list[str]:
    """Сервисы из `passwords-changed` каталога отчёта — их видит summary."""
    path = tmp_path / "report" / "passwords-changed"
    return path.read_text().split() if path.exists() else []


def test_new_secrets_reach_the_database_and_the_broker(tmp_path: Path) -> None:
    result, state = run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "pg").read_text() == NEW_PG
    assert (state / "rabbit").read_text() == NEW_RABBIT
    assert "postgres: пароль роли dmc268 сменён" in result.stdout
    assert "rabbitmq: пароль пользователя dmc268 сменён" in result.stdout
    assert changed(tmp_path) == ["postgres", "rabbitmq"]
    # Первый вход ждёт отказа: его stderr в лог не идёт.
    assert "password authentication failed" not in result.stderr


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
    assert not (state / "pg.stdin").exists()
    assert not [c for c in calls(tmp_path).splitlines() if c.startswith("psql ") and " -h " not in c]
    assert not [c for c in calls(tmp_path).splitlines() if c.startswith("rabbitmqctl") and "change_password" in c]
    assert "postgres: пароль роли dmc268 совпадает с секретом" in result.stdout
    assert "rabbitmq: пароль пользователя dmc268 совпадает с секретом" in result.stdout
    assert changed(tmp_path) == []


def test_only_a_scram_hash_reaches_the_server(tmp_path: Path) -> None:
    """`\\password` шифрует пароль в psql: на сервер уходит `ALTER USER … PASSWORD 'SCRAM-SHA-256$…'`.

    Открытый пароль в тексте запроса попал бы в лог медленных и выборочных
    запросов и в `pg_stat_activity.query` — его видит роль `pg_monitor` (#130).
    """
    result, state = run(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr
    stdin = (state / "pg.stdin").read_text()
    assert stdin.splitlines()[0] == "\\password dmc268"
    psql_calls = [c for c in calls(tmp_path).splitlines() if c.startswith("psql ")]
    local = [c for c in psql_calls if " -h " not in c]
    assert local, psql_calls
    assert all("ON_ERROR_STOP=1" in c for c in local), local
    script = SCRIPT.read_text()
    for text in (script, calls(tmp_path), stdin.splitlines()[0]):
        assert not re.search(r"ALTER\s+(ROLE|USER)", text, re.IGNORECASE)
        assert not re.search(r"PASSWORD\s+[:']", text)
    # Вход по сети проверяется уже новым паролем — им придут API и миграции.
    assert "-h dmc268-postgres" in calls(tmp_path)


def test_a_password_that_did_not_take_stops_the_deploy(tmp_path: Path) -> None:
    result, state = run(tmp_path, PSQL_ALTER_NOOP="1")

    assert result.returncode != 0
    assert "::error::" in result.stdout
    # Причина неудачного входа — в логе шага, а не заглушена.
    assert "password authentication failed" in result.stderr
    # До смены у брокера не дошло: деплой и так остановлен.
    assert (state / "rabbit").read_text() == OLD_RABBIT
    # Смена шла — summary должен знать, что деплой упал в окне смены.
    assert changed(tmp_path) == ["postgres"]


def test_a_missing_container_changes_no_password(tmp_path: Path) -> None:
    """Оба контейнера проверяются до первой смены: иначе шаг упал бы с новым паролем базы и старым у брокера."""
    for missing in ("dmc268-rabbitmq", "dmc268-postgres"):
        (tmp_path / "calls").unlink(missing_ok=True)
        result, state = run(tmp_path, DOCKER_MISSING=missing)

        assert result.returncode != 0, missing
        assert (state / "pg").read_text() == OLD_PG, missing
        assert (state / "rabbit").read_text() == OLD_RABBIT, missing
        assert changed(tmp_path) == [], missing


def test_the_broker_is_awaited_on_a_clean_server(tmp_path: Path) -> None:
    """На чистом сервере брокер только что создан, а `wait` у его контейнера нет."""
    result, state = run(tmp_path, AWAIT_FAILS="3")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (state / "rabbit").read_text() == NEW_RABBIT


def test_a_broker_that_never_starts_stops_the_deploy(tmp_path: Path) -> None:
    result, state = run(tmp_path, AWAIT_FAILS="1000")

    assert result.returncode != 0
    assert (state / "rabbit").read_text() == OLD_RABBIT
    assert (state / "pg").read_text() == OLD_PG


def test_a_password_with_whitespace_or_non_ascii_is_rejected_before_the_server(tmp_path: Path) -> None:
    """`urlencode` в tofu кодирует пробел как `+`, а SQLAlchemy и pika читают `+` буквально;
    `rabbitmqctl` обрезает пробелы по краям stdin. Такой пароль шаг выставил бы, а API не вошёл бы."""
    for name, value in (
        ("PG_PASSWORD", ""), ("PG_PASSWORD", "with space"), ("RABBITMQ_PASSWORD", " edge"),
        ("RABBITMQ_PASSWORD", "edge "), ("PG_PASSWORD", "tab\there"), ("RABBITMQ_PASSWORD", "a\nb"),
        ("PG_PASSWORD", "pass\u00e9"),
    ):
        (tmp_path / "calls").unlink(missing_ok=True)
        result, _ = run(tmp_path, **{name: value})

        assert result.returncode != 0, (name, value)
        assert f"::error::{name}" in result.stdout, (name, value)
        assert calls(tmp_path) == "", (name, value)


def test_printable_ascii_passwords_reach_both(tmp_path: Path) -> None:
    """`+`, `/`, `=`, `@`, `:`, `%`, кавычки и `\\` проходят: base64-секреты стенда не ломаются."""
    for value in ACCEPTED:
        result, state = run(tmp_path, PG_PASSWORD=value, RABBITMQ_PASSWORD=value)

        assert result.returncode == 0, (value, result.stdout + result.stderr)
        assert (state / "pg").read_text() == value
        assert (state / "rabbit").read_text() == value
        assert value not in calls(tmp_path)


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
        assert re.search(r"^  lifecycle \{[^}]*^\s*ignore_changes\s*=\s*\[env\]$", body, re.MULTILINE), name


def deploy_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    return list(workflow["jobs"]["deploy"]["steps"])


def test_passwords_are_set_after_the_checked_plan_and_before_migrations() -> None:
    """После плана: отклонённый план пароль не трогает. До миграций: они входят уже новым."""
    ids = [s.get("id") for s in deploy_steps()]
    assert ids.index("plan") + 2 == ids.index("pull") + 1 == ids.index("passwords") == ids.index("migrate") - 1

    step = next(s for s in deploy_steps() if s.get("id") == "passwords")
    assert str(step["run"]).strip().splitlines()[-1] == f".github/scripts/deploy/{SCRIPT.name}"
    env = step["env"]
    assert isinstance(env, dict)
    assert env["DOCKER_HOST"] == "ssh://stand"
    assert env["PG_PASSWORD"] == "${{ secrets.PG_PASSWORD_DMC268_T1 }}"
    assert env["RABBITMQ_PASSWORD"] == "${{ secrets.RABBITMQ_PASSWORD_DMC268_T1 }}"
    # Брокер на чистом сервере ждём до двух минут; зависший ssh не держит job.
    assert step["timeout-minutes"] == 5


def test_the_api_image_is_pulled_before_the_password_window() -> None:
    """Окно смены — от шага паролей до замены API: pull образа в него не входит."""
    step = next(s for s in deploy_steps() if s.get("id") == "pull")
    assert step["env"] == {"API_IMAGE": "${{ needs.image.outputs.image }}", "DOCKER_HOST": "ssh://stand"}
    assert str(step["run"]).strip().splitlines()[-1] == 'docker pull --quiet "$API_IMAGE"'
    assert step["timeout-minutes"] == 5
    migrate = Path(".github/scripts/deploy/migrate.sh").read_text()
    assert not re.search(r"^\s*docker pull\b", migrate, re.MULTILINE)


def test_a_new_user_or_database_stops_the_plan() -> None:
    """`ignore_changes = [env]` прятал бы и смену пользователя или базы: пароль сменился бы
    у старой роли, а миграции вошли бы новой. Такую смену plan отклоняет постусловием: при
    ignore_changes `self.env` — окружение, с которым контейнер создан (проверено на dind)."""
    for name, keys in (("postgres", ("POSTGRES_USER", "POSTGRES_DB")), ("rabbitmq", ("RABBITMQ_DEFAULT_USER",))):
        body = container_block(name)
        post = re.search(r"^    postcondition \{\n(.*?)^    \}", body, re.MULTILINE | re.DOTALL)
        assert post, name
        for key in keys:
            assert re.search(rf'contains\(self\.env, "{key}=\$\{{var\.\w+\}}"\)', post.group(1)), (name, key)
        assert "PASSWORD" not in post.group(1), "пароль сверять нельзя: его смена — обычный деплой"
        assert not re.search(r"^\s*replace_triggered_by\s*=", body, re.MULTILINE), "первое создание триггера пересоздало бы базу"
