"""Отчёт деплоя: сторож томов, разбивка простоя API и summary job'а `deploy`.

Сторож — единственное, что стоит между планом, уничтожающим том базы, и
`tofu apply`; узнавать о его поломке деплоем нельзя. Поэтому `plan.sh` и
`apply.sh` гоняются настоящим bash с подложными `tofu` и `ssh`, а вход —
настоящие данные: `tests/fixtures/deploy/plan-*.json` — урезанный до
`resource_changes` вывод `tofu show -json` OpenTofu 1.13.2 на демоне Docker
29.9.2 (выкат после #204 и план с заменой и удалением томов; `plan-infra-*` —
targeted-план шага «Поднять базу и брокер» без изменений и со сменой пароля
базы, #213), `api-*` — то, что
собрал `apply.sh` при таком выкате: события Docker, id старого контейнера и
состояние нового. `api-new-after-10min.json` — тот же контейнер, но inspect
через 10 мин после healthy (воркер дорабатывал сообщение): в журнале проверок
только пять успешных раз в 30 с, первая успешная вытеснена.

Тома защищает и `prevent_destroy` в `infra/*.tf`: `tofu test` его не проверит
(тест не может переименовать том, а уборка `run` с `command = apply` на нём
упала бы), поэтому наличие сверяется чтением конфигурации.

Отдельно сверяется раскладка job'а с `scripts/deploy_report.py`: summary
знает шаги по id, и шаг без id, засечки или строки в `STEP_RESULTS` молча
выпал бы из отчёта.
"""

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts import deploy_report
from tests.infra_text import read_infra_text

FIXTURES = Path("tests/fixtures/deploy")
SCRIPTS = Path(".github/scripts/deploy")
DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")

# Подложный tofu: `plan -out=<файл>` создаёт файл плана, `show -json`
# отдаёт фикстуру, `apply <файл>` без файла падает, а с ним — если велит
# тест. `output -raw` отдаёт окружение миграций. Вызовы — в журнал.
FAKE_TOFU = """#!/usr/bin/env bash
args="$*"
echo "$args" >> "$CALLS"
case "$args" in
  *" plan "*) out="${args##*-out=}"; touch "infra/${out%% *}" ;;
  *" show -json "*) cat "$PLAN_JSON" ;;
  *" apply "*) [ -e "infra/${args##* }" ] || exit 3; exit "${APPLY_EXIT:-0}" ;;
  *" output -raw migrate_env") echo "DATABASE_URL=postgresql+psycopg://u:p@dmc268-postgres:5432/d" ;;
esac
"""

# Подложный docker для migrate.sh: вызовы — в журнал, `run` печатает вывод
# alembic из MIGRATE_LOG и выходит с MIGRATE_EXIT, `rm` контейнера, которого
# нет, — код 1, как у настоящего.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$CALLS"
case "$1" in
  run) cat "${MIGRATE_LOG:-/dev/null}"; exit "${MIGRATE_EXIT:-0}" ;;
  rm) exit 1 ;;
esac
"""

# Подложный ssh: отвечает на команды apply.sh и summary.sh из файлов фикстур.
# SSH_FAIL — подстрока команды, на которой ssh падает, как при обрыве (255).
FAKE_SSH = """#!/usr/bin/env bash
while [ $# -gt 0 ]; do case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
shift
echo "$*" >> "$CALLS"
if [ -n "${SSH_FAIL:-}" ] && [[ "$*" == *"$SSH_FAIL"* ]]; then exit 255; fi
case "$*" in
  *"docker events"*) cat "$FIX/api-events.jsonl"; exec sleep 30 ;;
  *"docker inspect -f '{{.Id}}'"*) cat "$FIX/${OLD_ID:-api-old-id}" ;;
  *"docker inspect -f"*) cat "$FIX/${NEW:-api-new.json}" ;;
  *"docker info"*) printf 'docker_root /var/lib/docker\\n%s\\n' "$DF" ;;
esac
"""

# coreutils timeout на macOS нет: подложный пишет вызов и запускает команду.
FAKE_TIMEOUT = """#!/usr/bin/env bash
echo "timeout $*" >> "$CALLS"
shift
exec "$@"
"""

GNU_DF = """Filesystem     1024-blocks     Used Available Capacity Mounted on
/dev/vda1         81106868 30553012  50537472      38% /
/dev/vda1         81106868 30553012  50537472      38% /"""


def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("tofu", FAKE_TOFU), ("ssh", FAKE_SSH), ("timeout", FAKE_TIMEOUT), ("docker", FAKE_DOCKER)):
        path = bin_dir / name
        path.write_text(body)
        path.chmod(0o755)
    return bin_dir


def run_script(
    tmp_path: Path, script: str, *, plan: str = "plan-replace-api.json", **env: str
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    """Прогнать скрипт деплоя из копии репозитория; вернуть результат, каталог отчёта и корень."""
    root = tmp_path / "repo"
    (root / "infra").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "deploy_report.py").write_text(Path("scripts/deploy_report.py").read_text())
    report = tmp_path / "report"
    result = subprocess.run(
        ["bash", str((SCRIPTS / script).resolve())],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{fake_bin(tmp_path)}:{os.environ['PATH']}",
            "REPORT_DIR": str(report),
            "CALLS": str(tmp_path / "calls"),
            "PLAN_JSON": str((FIXTURES / plan).resolve()),
            "FIX": str(FIXTURES.resolve()),
            "DF": GNU_DF,
            **env,
        },
    )
    return result, report, root


# --- сторож и план ----------------------------------------------------------


def test_guard_passes_a_plan_that_keeps_the_volumes(tmp_path: Path) -> None:
    result, report, root = run_script(tmp_path, "plan.sh")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Тома данных план не уничтожает, базу и брокер не пересоздаёт без разрешения." in result.stdout
    assert json.loads((report / "plan.json").read_text()) == {
        "changes": {
            "docker_container": {"replace": 2},
            "docker_image": {"forget": 2},
            "terraform_data": {"change": 1},
        },
        "protected_deletes": [],
        "stateful_deletes": [],
        "stateful_allowed_by": None,
    }
    # Проверенный план остаётся для apply.sh.
    assert (root / "infra" / "tfplan").exists()


def test_guard_stops_a_plan_that_destroys_a_volume(tmp_path: Path) -> None:
    """Замена тома базы и удаление тома брокера — оба повода остановить деплой до apply."""
    result, report, root = run_script(tmp_path, "plan.sh", plan="plan-delete-volumes.json")

    assert result.returncode != 0
    assert "::error::план уничтожает том данных docker_volume.postgres" in result.stdout
    assert "::error::план уничтожает том данных docker_volume.rabbitmq" in result.stdout
    plan = json.loads((report / "plan.json").read_text())
    assert plan["protected_deletes"] == ["docker_volume.postgres", "docker_volume.rabbitmq"]
    assert plan["changes"]["docker_volume"] == {"replace": 1, "destroy": 1}
    # Отклонённый план с паролями внутри не остаётся на диске.
    assert not (root / "infra" / "tfplan").exists()


def test_guard_ignores_volumes_it_does_not_protect() -> None:
    plan = {"resource_changes": [
        {"address": "docker_volume.scratch", "type": "docker_volume", "change": {"actions": ["delete"]}},
        {"address": "docker_volume.postgres", "type": "docker_volume", "change": {"actions": ["no-op"]}},
    ]}
    assert deploy_report.protected_deletes(plan) == []
    assert deploy_report.plan_changes(plan) == {"docker_volume": {"destroy": 1}}


def test_guard_stops_a_full_plan_that_replaces_the_database(tmp_path: Path) -> None:
    """В плане с заменой тома пересоздаётся и контейнер postgres: это тоже повод остановиться."""
    result, report, _ = run_script(tmp_path, "plan.sh", plan="plan-delete-volumes.json")

    assert "::error::план пересоздаёт или удаляет docker_container.postgres" in result.stdout
    assert json.loads((report / "plan.json").read_text())["stateful_deletes"] == ["docker_container.postgres"]


# --- база и брокер до миграций (infra.sh) ------------------------------------


def test_targeted_plan_with_a_new_database_password_is_rejected(tmp_path: Path) -> None:
    """Сценарий #213: смена TF_VAR_postgres_password — `postgres must be replaced`.

    Фикстура — настоящий `tofu show -json` targeted-плана шага на docker:29-dind.
    Раньше targeted apply пересоздавал базу мимо всякой проверки, SIGKILL'ом.
    """
    result, report, root = run_script(tmp_path, "infra.sh", plan="plan-infra-replace-postgres.json")

    assert result.returncode != 0
    assert "::error::план пересоздаёт или удаляет docker_container.postgres" in result.stdout
    assert "#161" in result.stdout
    calls = (tmp_path / "calls").read_text()
    assert "-target=docker_container.postgres" in calls
    assert " apply " not in calls, calls
    plan = json.loads((report / "plan-infra.json").read_text())
    assert plan["stateful_deletes"] == ["docker_container.postgres"]
    assert plan["changes"] == {"docker_container": {"replace": 1}}
    # Файла отклонённого плана с паролями не остаётся.
    assert not list((root / "infra").glob("*tfplan*"))


def test_targeted_plan_without_changes_is_applied_as_checked(tmp_path: Path) -> None:
    result, report, root = run_script(tmp_path, "infra.sh", plan="plan-infra-no-changes.json")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    plan = next(c for c in calls if " plan " in c)
    assert all(f"-target={t}" in plan for t in (
        "docker_network.dmc268", "docker_container.postgres", "docker_container.rabbitmq",
    )), plan
    out = re.search(r"-out=(\S+)", plan)
    assert out, plan
    # Применяется ровно проверенный файл плана, без своих -target.
    assert calls[-1] == f"-chdir=infra apply -input=false -no-color {out.group(1)}", calls
    assert json.loads((report / "plan-infra.json").read_text())["changes"] == {}
    assert not list((root / "infra").glob("*tfplan*"))


def test_manual_override_lets_the_database_be_replaced_with_a_warning(tmp_path: Path) -> None:
    """Ручной запуск с allow_stateful_replace: замена postgres проходит, но громко."""
    result, report, root = run_script(
        tmp_path, "infra.sh", plan="plan-infra-replace-postgres.json",
        ALLOW_STATEFUL_REPLACE="true", DEPLOY_ACTOR="octocat",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "::warning::замена разрешена вручную (octocat): docker_container.postgres" in result.stdout
    assert "::error::" not in result.stdout
    assert (tmp_path / "calls").read_text().splitlines()[-1].endswith(" apply -input=false -no-color infra.tfplan")
    plan = json.loads((report / "plan-infra.json").read_text())
    assert plan["stateful_deletes"] == ["docker_container.postgres"]
    assert plan["stateful_allowed_by"] == "octocat"
    assert not list((root / "infra").glob("*tfplan*"))

    text = summary(tmp_path, report, JOB_STATUS="success", STEP_RESULTS=ALL_SUCCESS)
    assert "> Замена `docker_container.postgres` разрешена вручную: запуск с `allow_stateful_replace` (octocat)" in text
    assert "Сторож остановил деплой" not in text


def test_manual_override_never_lets_a_volume_go(tmp_path: Path) -> None:
    """Флаг — про контейнеры; тома с данными он не открывает."""
    result, _, root = run_script(
        tmp_path, "plan.sh", plan="plan-delete-volumes.json",
        ALLOW_STATEFUL_REPLACE="true", DEPLOY_ACTOR="octocat",
    )

    assert result.returncode != 0
    assert "::error::план уничтожает том данных docker_volume.postgres" in result.stdout
    assert "::warning::замена разрешена вручную (octocat): docker_container.postgres" in result.stdout
    assert not (root / "infra" / "tfplan").exists()


def test_only_the_literal_true_opens_the_guard(tmp_path: Path) -> None:
    """Push-деплой получает `false` из выражения в env — и пустое, и чужое значение сторож не открывают."""
    for value in ("false", "", "1", "True "):
        result, _, _ = run_script(
            tmp_path, "infra.sh", plan="plan-infra-replace-postgres.json", ALLOW_STATEFUL_REPLACE=value,
        )
        assert result.returncode != 0, value
        assert "::error::план пересоздаёт или удаляет docker_container.postgres" in result.stdout


def test_override_reaches_the_guard_only_from_a_manual_run() -> None:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    triggers = workflow[True]  # PyYAML читает ключ `on` как True
    flag = triggers["workflow_dispatch"]["inputs"]["allow_stateful_replace"]
    assert flag["type"] == "boolean" and flag["default"] is False
    for step_id in ("infra", "plan"):
        step = next(s for s in deploy_steps() if s.get("id") == step_id)
        env = step["env"]
        assert isinstance(env, dict)
        assert env["ALLOW_STATEFUL_REPLACE"] == (
            "${{ github.event_name == 'workflow_dispatch' && inputs.allow_stateful_replace }}"
        ), step_id
        assert "${{" not in str(step["run"]), step_id


def test_stateful_deletes_cover_replace_and_delete_but_not_update() -> None:
    def plan(*changes: tuple[str, list[str]]) -> dict[str, object]:
        return {"resource_changes": [
            {"address": a, "type": a.split(".")[0], "change": {"actions": actions}} for a, actions in changes
        ]}

    assert deploy_report.stateful_deletes(plan(
        ("docker_container.postgres", ["delete", "create"]),
        ("docker_container.rabbitmq", ["delete"]),
        ("docker_container.api", ["delete", "create"]),
    )) == ["docker_container.postgres", "docker_container.rabbitmq"]
    # destroy_grace_seconds добавляется на месте: такой план проходит.
    assert deploy_report.stateful_deletes(plan(
        ("docker_container.postgres", ["update"]),
        ("docker_container.rabbitmq", ["create"]),
    )) == []


def test_summary_shows_the_rejected_targeted_plan(tmp_path: Path) -> None:
    _, report, _ = run_script(tmp_path, "infra.sh", plan="plan-infra-replace-postgres.json")
    results = ALL_SUCCESS.replace("infra success success", "infra failure failure")

    text = summary(tmp_path, report, JOB_STATUS="failure", STEP_RESULTS=results)

    assert "#### База и брокер" in text
    assert "> Сторож остановил деплой до apply: план пересоздаёт или удаляет `docker_container.postgres`" in text
    assert "#### Полный выкат" in text


def volume_blocks() -> dict[str, str]:
    """Ресурсы docker_volume из infra/*.tf: адрес → тело блока."""
    return {
        f"docker_volume.{name}": body
        for name, body in re.findall(
            r'^resource\s+"docker_volume"\s+"(\w+)"\s*\{\n(.*?)^\}', read_infra_text(), re.MULTILINE | re.DOTALL
        )
    }


def test_guard_protects_every_volume_the_stack_declares() -> None:
    """Новый том или переименованный ресурс тома без правки сторожа — дыра в защите."""
    declared = volume_blocks()
    assert declared, "ресурсы docker_volume не найдены в infra/*.tf"
    assert set(declared) == set(deploy_report.PROTECTED)


def test_every_protected_volume_prevents_destroy() -> None:
    """Сторож в plan.sh не видит targeted apply шага «Поднять базу и брокер»: том там держит prevent_destroy."""
    blocks = volume_blocks()
    for address in deploy_report.PROTECTED:
        assert address in blocks, f"{address} из PROTECTED не найден в infra/*.tf"
        assert re.search(
            r"^\s*lifecycle\s*\{[^}]*^\s*prevent_destroy\s*=\s*true\s*$", blocks[address], re.MULTILINE
        ), f"у {address} нет lifecycle {{ prevent_destroy = true }}"


# --- apply и простой API -----------------------------------------------------


def test_apply_runs_the_saved_plan_and_collects_the_api_containers(tmp_path: Path) -> None:
    _, _, root = run_script(tmp_path, "plan.sh")
    result, report, _ = run_script(tmp_path, "apply.sh")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text()
    assert "-chdir=infra apply -input=false -no-color tfplan" in calls
    assert (report / "api-old-id").read_text() == (FIXTURES / "api-old-id").read_text()
    assert json.loads((report / "api-new.json").read_text())["Id"].startswith("a4964462")
    assert "health_status: healthy" in (report / "api-events.jsonl").read_text()
    assert not (root / "infra" / "tfplan").exists()


def test_apply_keeps_the_tofu_exit_code_and_still_collects(tmp_path: Path) -> None:
    run_script(tmp_path, "plan.sh")
    result, report, root = run_script(tmp_path, "apply.sh", APPLY_EXIT="1")

    assert result.returncode == 1
    assert (report / "api-new.json").exists()
    assert not (root / "infra" / "tfplan").exists()


def test_apply_reads_only_state_of_the_new_container() -> None:
    """В полном `docker inspect` есть окружение с паролями: в файл отчёта его не берём."""
    script = (SCRIPTS / "apply.sh").read_text()
    inspects = re.findall(r"^\s*ask \S+ \"docker inspect[^\n]*", script, re.MULTILINE)
    assert len(inspects) == 2, inspects
    assert all(" -f " in call for call in inspects), inspects


def test_apply_asks_the_server_under_a_timeout(tmp_path: Path) -> None:
    """ConnectTimeout через живой ControlMaster не действует: вопросы серверу — под timeout."""
    run_script(tmp_path, "plan.sh")
    run_script(tmp_path, "apply.sh")

    calls = (tmp_path / "calls").read_text()
    assert calls.count("timeout 30 ssh stand docker inspect") == 2, calls


def test_apply_tells_a_failed_ssh_from_a_missing_container(tmp_path: Path) -> None:
    run_script(tmp_path, "plan.sh")
    result, report, _ = run_script(tmp_path, "apply.sh", SSH_FAIL="{{.Id}}")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (report / "api-old-id.error").read_text().strip() == "255"
    text = summary(tmp_path, report, JOB_STATUS="success", STEP_RESULTS=ALL_SUCCESS)
    assert "Не удалось спросить сервер о прежнем контейнере API (ssh или timeout, код 255)" in text
    assert "создан впервые" not in text


def test_apply_without_the_old_container_is_a_first_deploy(tmp_path: Path) -> None:
    """Контейнера нет — ssh ответил, а docker inspect пуст: это первый выкат, не сбой."""
    run_script(tmp_path, "plan.sh")
    # Нет файла — подложный ssh отвечает пусто с кодом 1, как `docker inspect` без контейнера.
    result, report, _ = run_script(tmp_path, "apply.sh", OLD_ID="absent")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (report / "api-old-id.error").exists()
    text = summary(tmp_path, report, JOB_STATUS="success", STEP_RESULTS=ALL_SUCCESS)
    assert "API создан впервые: прежнего контейнера не было" in text


OLD_ID = (FIXTURES / "api-old-id").read_text().strip()


def fixture_events() -> list[dict[str, object]]:
    return deploy_report.read_events((FIXTURES / "api-events.jsonl").read_text())


def old_events_only() -> list[dict[str, object]]:
    """Поток, в котором нет событий нового контейнера: он оборвался после остановки старого."""
    return [e for e in fixture_events() if e["Actor"]["ID"] == OLD_ID]  # type: ignore[index]


def load_downtime(
    events: list[dict[str, object]] | None = None, new_file: str = "api-new.json", **changes: object
) -> dict[str, object]:
    new = json.loads((FIXTURES / new_file).read_text())
    new.update(changes)
    return deploy_report.api_downtime(OLD_ID, fixture_events() if events is None else events, new)


def test_downtime_breakdown_from_real_events() -> None:
    d = load_downtime()

    assert d["status"] == "replaced"
    assert d == pytest.approx({
        **d,
        "stop": 1.14,
        "recreate": 0.052,
        "start": 0.033,
        "ready": 3.904,
        "total": 5.13,
        "check": 0.238,
    }, abs=0.002)
    assert d["failed_checks"] == 2
    assert not d["failed_checks_at_least"]
    assert d["healthy_from"] == "events" and d["healthy_reliable"]
    parts = (d["stop"], d["recreate"], d["start"], d["ready"])
    assert sum(parts) == pytest.approx(d["total"], abs=0.005)  # type: ignore[arg-type]


def test_downtime_does_not_grow_when_apply_ends_long_after_healthy() -> None:
    """Сценарий ревью #210: inspect через 10 мин после healthy, первая успешная из журнала вытеснена.

    По журналу простой вышел бы ≈ 485 с; по событию `health_status: healthy` — настоящие 5,1 с.
    """
    d = load_downtime(new_file="api-new-after-10min.json")

    assert d["total"] == pytest.approx(5.13, abs=0.002)
    assert d["ready"] == pytest.approx(3.904, abs=0.002)
    assert d["failed_checks"] == 2 and d["healthy_from"] == "events" and d["healthy_reliable"]


def test_downtime_from_the_health_log_is_flagged_when_the_log_is_full() -> None:
    """Без событий нового контейнера — запасной путь по журналу; полный журнал — пометка о недостоверности."""
    d = load_downtime(old_events_only(), new_file="api-new-after-10min.json")

    assert d["healthy_from"] == "health_log"
    assert d["total"] > 480  # type: ignore[operator]
    assert not d["healthy_reliable"] and d["failed_checks_at_least"]


def test_downtime_from_a_short_health_log_is_exact() -> None:
    """Журнал не полон — ничего не вытеснено, и запасной путь даёт то же, что события."""
    d = load_downtime(old_events_only())

    assert d["healthy_from"] == "health_log" and d["healthy_reliable"]
    assert d["total"] == pytest.approx(5.13, abs=0.002)
    assert d["check"] == pytest.approx(0.238, abs=0.002)
    assert d["failed_checks"] == 2 and not d["failed_checks_at_least"]


def test_downtime_when_the_new_api_never_became_healthy() -> None:
    """Поток видел старт нового и только неуспешные проверки: healthy не было, счёт неуспешных точный."""
    events = []
    for e in fixture_events():
        if e["Actor"]["ID"] == OLD_ID:  # type: ignore[index]
            events.append(e)
        elif e["Action"] != "health_status: healthy":
            attributes = {**e["Actor"]["Attributes"]}  # type: ignore[index]
            if e["Action"] == "exec_die":
                attributes["exitCode"] = "1"
            events.append({**e, "Actor": {**e["Actor"], "Attributes": attributes}})  # type: ignore[dict-item]
    d = load_downtime(events)

    assert d["ready"] is None and d["total"] is None and d["check"] is None
    assert d["stop"] == pytest.approx(1.14, abs=0.002)
    assert d["failed_checks"] == 3 and not d["failed_checks_at_least"]
    assert d["healthy_from"] == "events"


def test_downtime_when_the_api_was_not_recreated() -> None:
    new = json.loads((FIXTURES / "api-new.json").read_text())
    assert deploy_report.api_downtime(new["Id"], [], new) == {"status": "kept"}
    assert deploy_report.api_downtime("", [], new) == {"status": "created"}
    assert deploy_report.api_downtime("abc", [], None) == {"status": "missing"}


def test_health_log_times_with_a_zone_offset() -> None:
    """Время в журнале проверок Docker пишет в зоне демона, а не всегда в UTC."""
    assert deploy_report.parse_time("2026-10-25T14:40:33.125921014+03:00") == pytest.approx(
        deploy_report.parse_time("2026-10-25T11:40:33.125921014Z")
    )


# --- миграции, диск, засечки --------------------------------------------------


def test_migrations_from_alembic_output() -> None:
    log = (
        "INFO  [alembic.runtime.migration] Will assume transactional DDL.\n"
        "INFO  [alembic.runtime.migration] Running upgrade 0011_a -> 0012_b, треды\n"
        "INFO  [alembic.runtime.migration] Running upgrade 0012_b -> 0013_c, индекс\n"
    )
    assert deploy_report.migrations(log) == {"applied": 2, "from": "0011_a", "to": "0013_c"}
    assert deploy_report.migrations("INFO  [alembic.runtime.migration] Running upgrade  -> 0001_init, x\n") == {
        "applied": 1, "from": "пустая база", "to": "0001_init",
    }
    assert deploy_report.migrations("INFO  Will assume transactional DDL.\n") == {"applied": 0}


FAILED_MIGRATION_LOG = (
    "INFO  [alembic.runtime.migration] Will assume transactional DDL.\n"
    "INFO  [alembic.runtime.migration] Running upgrade 0012_b -> 0013_c, таблица\n"
    "INFO  [alembic.runtime.migration] Running upgrade 0013_c -> 0014_d, деление на ноль\n"
    "Traceback (most recent call last):\n"
    "sqlalchemy.exc.DataError: (psycopg.errors.DivisionByZero) division by zero\n"
)


def test_failed_migrations_applied_nothing() -> None:
    """Сценарий #213: alembic идёт одной транзакцией, упавшая 0014 откатывает и 0013."""
    assert deploy_report.migrations(FAILED_MIGRATION_LOG, "failure") == {
        "applied": 0, "from": "0012_b", "failed": "0014_d",
    }
    # Упало до первой ревизии — например, lock_timeout на чтении alembic_version.
    assert deploy_report.migrations("INFO  Will assume transactional DDL.\nLockNotAvailable\n", "failure") == {
        "applied": 0,
    }


def test_summary_of_failed_migrations_does_not_claim_revisions(tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "migrate.log").write_text(FAILED_MIGRATION_LOG)
    results = ALL_SUCCESS.replace("migrate success success", "migrate failure failure")

    text = summary(tmp_path, report, JOB_STATUS="failure", STEP_RESULTS=results)

    assert "| Применено ревизий | `dmc268.deploy.migrations.applied` | 0 |" in text
    assert "| Упала на ревизии | `dmc268.deploy.migrations.revision.failed` | `0014_d` |" in text
    assert "| Ревизия базы | `dmc268.deploy.migrations.revision.from` | `0012_b` |" in text
    assert "миграции откатены" in text
    assert "dmc268.deploy.migrations.revision.to" not in text


def test_summary_of_migrations_that_failed_before_alembic(tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "migrate.log").write_text("docker: Error response from daemon: Conflict.\n")
    results = ALL_SUCCESS.replace("migrate success success", "migrate failure failure")

    text = summary(tmp_path, report, JOB_STATUS="failure", STEP_RESULTS=results)

    assert "| Применено ревизий | `dmc268.deploy.migrations.applied` | 0 |" in text
    assert "до первой ревизии" in text


def test_summary_of_cancelled_migrations_claims_nothing(tmp_path: Path) -> None:
    report = tmp_path / "report"
    report.mkdir()
    (report / "migrate.log").write_text(FAILED_MIGRATION_LOG)
    results = ALL_SUCCESS.replace("migrate success success", "migrate cancelled cancelled")

    text = summary(tmp_path, report, JOB_STATUS="cancelled", STEP_RESULTS=results)

    assert "Шаг миграций прерван" in text
    assert "dmc268.deploy.migrations.applied" not in text


# --- миграции: скрипт шага ---------------------------------------------------


def test_migrate_removes_a_container_left_by_a_killed_run(tmp_path: Path) -> None:
    """Сценарий #213: job убит по таймауту — `dmc268-migrations` остаётся, следующий деплой падал на имени."""
    result, report, _ = run_script(
        tmp_path, "migrate.sh",
        API_IMAGE="ghcr.io/o/r:t@sha256:" + "0" * 64, DOCKER_HOST="ssh://stand",
        MIGRATE_LOG=str(tmp_path / "alembic.log"),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "calls").read_text().splitlines()
    run = next(i for i, c in enumerate(calls) if c.startswith("docker run "))
    assert "docker rm -f dmc268-migrations" in calls[:run], calls
    assert (report / "migrate.log").exists()


def test_migrate_keeps_the_alembic_exit_code(tmp_path: Path) -> None:
    (tmp_path / "alembic.log").write_text(FAILED_MIGRATION_LOG)
    result, report, _ = run_script(
        tmp_path, "migrate.sh",
        API_IMAGE="ghcr.io/o/r:t@sha256:" + "0" * 64, DOCKER_HOST="ssh://stand",
        MIGRATE_LOG=str(tmp_path / "alembic.log"), MIGRATE_EXIT="1",
    )

    assert result.returncode == 1
    assert "0014_d" in (report / "migrate.log").read_text()


def test_disk_rows_merge_when_docker_lives_on_the_root() -> None:
    rows = deploy_report.disk(f"docker_root /var/lib/docker\n{GNU_DF}")
    assert rows == [{
        "mountpoint": "/",
        "free": 50537472 * 1024,
        "utilization": pytest.approx(30553012 / (30553012 + 50537472)),
        "what": ["корень", "Docker (`/var/lib/docker`)"],
    }]


def test_disk_rows_for_a_separate_docker_partition() -> None:
    rows = deploy_report.disk(
        "docker_root /srv/docker\nFilesystem 1024-blocks Used Available Capacity Mounted on\n"
        "/dev/vda1 100 60 40 60% /\n/dev/vdb1 1000 100 900 10% /srv\n"
    )
    assert [(r["mountpoint"], r["what"]) for r in rows] == [
        ("/", ["корень"]),
        ("/srv", ["Docker (`/srv/docker`)"]),
    ]


def test_durations_run_to_the_next_mark(tmp_path: Path) -> None:
    for step in ("start", "migrate"):
        subprocess.run(
            ["bash", str(SCRIPTS / "mark.sh")],
            env={"PATH": os.environ["PATH"], "REPORT_DIR": str(tmp_path), "GITHUB_ACTION": step},
            check=True,
        )
    marks = deploy_report.read_marks((tmp_path / "marks").read_text())
    assert list(marks) == ["start", "migrate"]
    assert deploy_report.durations({"start": 10.0, "migrate": 12.5, "summary": 20.0}) == {
        "start": 2.5, "migrate": 7.5,
    }


# --- summary -------------------------------------------------------------------

ALL_SUCCESS = "\n".join(f"{i} success success" for _, _, ids in deploy_report.STEPS for i in ids)


def summary(tmp_path: Path, report: Path, **env: str) -> str:
    out = tmp_path / "summary.md"
    root = tmp_path / "repo"
    (root / "infra").mkdir(parents=True, exist_ok=True)
    (root / "scripts").mkdir(exist_ok=True)
    (root / "scripts" / "deploy_report.py").write_text(Path("scripts/deploy_report.py").read_text())
    result = subprocess.run(
        ["bash", str((SCRIPTS / "summary.sh").resolve())],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": f"{fake_bin(tmp_path)}:{os.environ['PATH']}",
            "REPORT_DIR": str(report),
            "GITHUB_STEP_SUMMARY": str(out),
            "CALLS": str(tmp_path / "calls"),
            "FIX": str(FIXTURES.resolve()),
            "DF": GNU_DF,
            "IMAGE": "ghcr.io/o/r:0a83d25@sha256:" + "0" * 64,
            **env,
        },
    )
    assert result.returncode == 0, result.stderr
    return out.read_text()


def test_summary_of_a_successful_deploy(tmp_path: Path) -> None:
    run_script(tmp_path, "plan.sh")
    _, report, _ = run_script(tmp_path, "apply.sh")
    (report / "migrate.log").write_text("INFO  Running upgrade 0011_a -> 0012_b, x\n")
    (report / "marks").write_text("start 100\nmigrate 110\nplan 118\napply 125\nsummary 160\n")

    text = summary(tmp_path, report, JOB_STATUS="success", STEP_RESULTS=ALL_SUCCESS)

    assert text.startswith("## Деплой: успешно\n")
    assert "| **Простой: SIGTERM старому → первая успешная проверка нового** | `dmc268.deploy.api.downtime` | **5.1** |" in text
    assert "`StartedAt` → `health_status: healthy`" in text
    assert "[!WARNING]" not in text
    assert "| `docker_container` |  |  |  | 2 |  |" in text
    assert "| Применено ревизий | `dmc268.deploy.migrations.applied` | 1 |" in text
    assert "| `/` | корень, Docker (`/var/lib/docker`) | 48.2 ГиБ | 38% |" in text
    assert "| Применить конфигурацию | `success` | 35.0 |" in text
    assert "| Длительность job'а, с | `dmc268.deploy.duration` | 60.0 |" in text
    assert "`container.image.tags` | `0a83d25`" in text


def test_summary_names_the_failed_step_even_without_a_report(tmp_path: Path) -> None:
    results = ALL_SUCCESS.replace("plan success success", "plan failure failure")
    results = re.sub(r"^(apply|check_api|check_worker|prune) success success$", r"\1 skipped skipped", results, flags=re.MULTILINE)

    text = summary(tmp_path, tmp_path / "empty", JOB_STATUS="failure", STEP_RESULTS=results)

    assert text.startswith("## Деплой: упал на шаге «Спланировать и проверить план»\n")
    assert "| Шаг падения | `error.type` | `plan` — Спланировать и проверить план |" in text
    assert "Apply не запускался." in text
    assert "| Применить конфигурацию | `skip` | — |" in text


# --- раскладка job'а -----------------------------------------------------------


def deploy_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(DEPLOY_WORKFLOW.read_text())
    return list(workflow["jobs"]["deploy"]["steps"])


def test_every_deploy_step_is_known_to_the_summary() -> None:
    steps = deploy_steps()
    summary_step = steps[-1]
    assert summary_step["id"] == deploy_report.SUMMARY_MARK
    assert summary_step["if"] == "always()"

    ids = [str(s.get("id", "")) for s in steps[:-1]]
    assert "" not in ids, f"шаг без id не попадёт в summary: {[s.get('name') or s.get('uses') for s in steps]}"
    known = [i for _, _, group in deploy_report.STEPS for i in group]
    assert ids == known, "порядок и состав шагов в deploy.yml и STEPS в scripts/deploy_report.py разошлись"

    env = summary_step["env"]
    assert isinstance(env, dict)
    lines = str(env["STEP_RESULTS"]).splitlines()
    assert lines == [f"{i} ${{{{ steps.{i}.outcome }}}} ${{{{ steps.{i}.conclusion }}}}" for i in ids]


def test_summary_labels_are_the_step_names() -> None:
    names = {str(s["id"]): s.get("name") for s in deploy_steps()}
    for mark, label, group in deploy_report.STEPS:
        if len(group) == 1:
            assert names[mark] == label, f"подпись шага {mark} в summary разошлась с name в deploy.yml"


def test_every_run_step_marks_its_start_first() -> None:
    """Шаг без засечки отдал бы своё время предыдущему."""
    for step in deploy_steps():
        if "run" not in step or step["id"] == "start":
            continue
        first = str(step["run"]).strip().splitlines()[0]
        assert first.endswith(".github/scripts/deploy/mark.sh"), f"шаг {step['id']}: первая строка {first!r}"


def test_tofu_runs_without_the_wrapper() -> None:
    """Обёртка setup-opentofu кладёт stdout tofu в выходы шага, а в нём пароли."""
    setup = next(s for s in deploy_steps() if str(s.get("uses", "")).startswith("opentofu/setup-opentofu@"))
    assert setup["with"]["tofu_wrapper"] is False  # type: ignore[index]


def test_summary_warns_when_readiness_comes_from_a_full_health_log(tmp_path: Path) -> None:
    run_script(tmp_path, "plan.sh")
    _, report, _ = run_script(tmp_path, "apply.sh", NEW="api-new-after-10min.json")
    events = (report / "api-events.jsonl").read_text().splitlines()
    (report / "api-events.jsonl").write_text("\n".join(e for e in events if OLD_ID in e) + "\n")

    text = summary(tmp_path, report, JOB_STATUS="success", STEP_RESULTS=ALL_SUCCESS)

    assert "`StartedAt` → `Health.Log[].End`" in text
    assert "> Готовность и простой недостоверны" in text


def test_summary_removes_a_plan_left_by_failed_migrations(tmp_path: Path) -> None:
    """План строится до миграций: упали они — apply.sh не шёл, файл плана с паролями убирает summary."""
    _, report, root = run_script(tmp_path, "plan.sh")
    assert (root / "infra" / "tfplan").exists()

    summary(tmp_path, report, JOB_STATUS="failure", STEP_RESULTS=ALL_SUCCESS)

    assert not (root / "infra" / "tfplan").exists()
    assert "timeout 30 ssh -o ConnectTimeout=10 stand" in (tmp_path / "calls").read_text()


def test_migrate_step_has_a_timeout() -> None:
    """Зависшая миграция держала бы job до его 60 минут, а блокировки — на горячих таблицах."""
    migrate = next(s for s in deploy_steps() if s.get("id") == "migrate")
    assert migrate["timeout-minutes"] == 10


def test_infra_step_runs_the_checked_targeted_plan() -> None:
    infra = next(s for s in deploy_steps() if s.get("id") == "infra")
    assert str(infra["run"]).strip().splitlines()[-1] == ".github/scripts/deploy/infra.sh"
    assert "tofu apply" not in str(infra["run"])


def test_summary_step_has_a_timeout() -> None:
    summary_step = deploy_steps()[-1]
    assert summary_step["timeout-minutes"] == 3
