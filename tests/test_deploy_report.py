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
from tests.shell_text import commands
from tests.test_stack_descriptions import missing_migration_restrictions

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

# Подложный docker для migrate.sh: вызовы — в журнал. `run … alembic
# upgrade head` печатает вывод alembic из MIGRATE_LOG и выходит с
# MIGRATE_EXIT; у N-й попытки — MIGRATE_LOG_N и MIGRATE_EXIT_N, если заданы.
# `run … alembic current` печатает CURRENT (с CURRENT_FAIL — код 255, как при
# оборванном ssh), `rm` контейнера, которого нет, — код 1.
FAKE_DOCKER = """#!/usr/bin/env bash
echo "docker $*" >> "$CALLS"
case "$1" in
  run)
    case "$*" in
      *"alembic current"*) [ -z "${CURRENT_FAIL:-}" ] || exit 255; printf '%s\\n' "${CURRENT:-}" ;;
      *)
        n=$(( $(cat "$CALLS.upgrades" 2>/dev/null || echo 0) + 1 )); echo "$n" > "$CALLS.upgrades"
        log="MIGRATE_LOG_$n" code="MIGRATE_EXIT_$n"
        cat "${!log:-${MIGRATE_LOG:-/dev/null}}"; exit "${!code:-${MIGRATE_EXIT:-0}}" ;;
    esac ;;
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


def test_guard_stops_a_volume_deleted_under_any_address() -> None:
    """Ресурс тома переименовали без `moved` — и адрес, и имя тома новые: план уничтожает
    старый том по адресу и имени, которых в конфигурации уже нет. Сторож ловит любой том."""
    plan = {"resource_changes": [
        {"address": "docker_volume.mq_v44", "type": "docker_volume",
         "change": {"actions": ["delete"], "before": {"name": "dmc268-rabbitmq-v44-data"}}},
        {"address": "docker_volume.mq_v45", "type": "docker_volume", "change": {"actions": ["create"]}},
        {"address": "module.x.docker_volume.pg", "type": "docker_volume", "change": {"actions": ["delete", "create"]}},
    ]}
    assert deploy_report.protected_deletes(plan) == ["docker_volume.mq_v44", "module.x.docker_volume.pg"]


def test_guard_allows_what_does_not_destroy_a_volume() -> None:
    """Снятие тома с учёта (`removed`, `destroy = false`) — `forget`; удаление контейнера — не том."""
    plan = {"resource_changes": [
        {"address": "docker_volume.postgres", "type": "docker_volume", "change": {"actions": ["forget"]}},
        {"address": "docker_volume.rabbitmq", "type": "docker_volume", "change": {"actions": ["no-op"]}},
        {"address": "docker_container.postgres", "type": "docker_container", "change": {"actions": ["delete"]}},
    ]}
    assert deploy_report.protected_deletes(plan) == []


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
        "docker_network.dmc268", "docker_network.backend", "docker_container.postgres", "docker_container.rabbitmq",
    )), plan
    out = re.search(r"-out=(\S+)", plan)
    assert out, plan
    # Применяется ровно проверенный файл плана, без своих -target.
    assert calls[-1] == f"-chdir=infra apply -input=false -no-color -lock-timeout=2m {out.group(1)}", calls
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
    assert (tmp_path / "calls").read_text().splitlines()[-1].endswith(" apply -input=false -no-color -lock-timeout=2m infra.tfplan")
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
        # Re-run прогона повторяет его inputs: флаг — только у первой попытки,
        # повтор с флагом — новым dispatch (ревью #220).
        assert env["ALLOW_STATEFUL_REPLACE"] == (
            "${{ github.event_name == 'workflow_dispatch' && github.run_attempt == 1"
            " && inputs.allow_stateful_replace }}"
        ), step_id
        assert env["DEPLOY_ACTOR"] == "${{ github.triggering_actor }}", step_id
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


def test_stateful_guard_follows_the_container_name_not_the_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ревью #220: ресурс базы переименовали без `moved` и поправили STATEFUL.

    План уничтожает контейнер по старому адресу, которого в списке уже нет, и
    создаёт по новому — замена базы шла бы мимо сторожа. Имя контейнера на
    хосте уникально: оно и ловит. Форма `change` — как у `tofu show -json`.
    """
    monkeypatch.setattr(deploy_report, "STATEFUL", ("docker_container.db", "docker_container.rabbitmq"))
    plan = {"resource_changes": [
        {"address": "docker_container.postgres", "type": "docker_container",
         "change": {"actions": ["delete"], "before": {"name": "dmc268-postgres"}, "after": None}},
        {"address": "docker_container.db", "type": "docker_container",
         "change": {"actions": ["create"], "before": None, "after": {"name": "dmc268-postgres"}}},
    ]}
    assert deploy_report.stateful_deletes(plan) == ["docker_container.postgres"]
    # Имя у заменяемого контейнера сменилось — ловит и новое имя, и адрес.
    assert deploy_report.stateful_deletes({"resource_changes": [
        {"address": "docker_container.api", "type": "docker_container",
         "change": {"actions": ["delete", "create"], "before": {"name": "dmc268-api"},
                    "after": {"name": "dmc268-rabbitmq"}}},
        {"address": "docker_container.db", "type": "docker_container",
         "change": {"actions": ["delete", "create"], "before": {"name": "dmc268-db"}, "after": {"name": "x"}}},
    ]}) == ["docker_container.api", "docker_container.db"]
    # Чужой ресурс с тем же атрибутом name — не контейнер.
    assert deploy_report.stateful_deletes({"resource_changes": [
        {"address": "docker_volume.x", "type": "docker_volume",
         "change": {"actions": ["delete"], "before": {"name": "dmc268-postgres"}, "after": None}},
    ]}) == []
    # Урезанный план без before/after (фикстуры) — по адресу, как раньше.
    assert deploy_report.stateful_deletes({"resource_changes": [
        {"address": "docker_container.db", "type": "docker_container", "change": {"actions": ["delete"]}},
    ]}) == ["docker_container.db"]


def test_stateful_names_are_the_names_of_the_stateful_containers() -> None:
    """Имена в STATEFUL_NAMES — `name` контейнеров из STATEFUL в infra/*.tf."""
    text = read_infra_text()
    names = set()
    for address in deploy_report.STATEFUL:
        body = re.search(
            rf'^resource\s+"docker_container"\s+"{address.split(".", 1)[1]}"\s*\{{\n(.*?)^\}}',
            text, re.MULTILINE | re.DOTALL,
        )
        assert body, address
        name = re.search(r'^\s*name\s*=\s*"([^"]+)"', body.group(1), re.MULTILINE)
        assert name, address
        names.add(name.group(1))
    assert names == set(deploy_report.STATEFUL_NAMES)


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


# Контейнеры на томах данных, которые деплой пересоздаёт без разрешения: API
# и воркер в них не ходят, данные — в защищённых томах. Реплика
# останавливается штатно (destroy_grace_seconds в infra/replica.tf), копия,
# оборванная на полпути, остаётся *.partial и убирается следующим стартом. До
# миграций они не нужны, поэтому и не в -target у infra.sh.
REPLACEABLE_ON_VOLUMES = frozenset({"docker_container.postgres_replica", "docker_container.postgres_backup"})


def test_every_container_on_a_data_volume_is_classified() -> None:
    """Ревью #220: контейнер на томе данных вне STATEFUL сторож пересоздал бы молча.

    Каждый такой контейнер — либо в STATEFUL и в -target у infra.sh, либо в
    REPLACEABLE_ON_VOLUMES с причиной; новый контейнер на томе без решения
    роняет тест. Тома — все `docker_volume` из infra/*.tf, как у сторожа томов:
    так тест не зависит от того, как сторож томов перечисляет защищённые (#219).
    """
    text = read_infra_text()
    volumes = set(re.findall(r'^resource\s+"docker_volume"\s+"(\w+)"', text, re.MULTILINE))
    assert volumes, "ресурсы docker_volume не найдены в infra/*.tf"
    containers = re.findall(r'^resource\s+"docker_container"\s+"(\w+)"\s*\{\n(.*?)^\}', text, re.MULTILINE | re.DOTALL)
    on_volumes = {
        f"docker_container.{name}"
        for name, body in containers
        if set(re.findall(r"volume_name\s*=\s*docker_volume\.(\w+)\.", body)) & volumes
    }
    stateful = set(deploy_report.STATEFUL)
    assert not stateful & REPLACEABLE_ON_VOLUMES
    assert on_volumes == stateful | REPLACEABLE_ON_VOLUMES, on_volumes ^ (stateful | REPLACEABLE_ON_VOLUMES)
    targets = set(re.findall(r"^\s*-target=(\S+)", (SCRIPTS / "infra.sh").read_text(), re.MULTILINE))
    assert stateful <= targets, stateful - targets
    assert not REPLACEABLE_ON_VOLUMES & targets, "реплика и копии до миграций не нужны"
    # Сторож ловит и по имени контейнера: у заменяемых оно своё.
    for address in REPLACEABLE_ON_VOLUMES:
        body = dict(containers)[address.split(".", 1)[1]]
        name = re.search(r'^\s*name\s*=\s*"([^"]+)"', body, re.MULTILINE)
        assert name and name.group(1) not in deploy_report.STATEFUL_NAMES, address


def test_targeted_step_brings_up_every_network_of_the_database_and_broker() -> None:
    """База — только в сети данных (#102): без её -target на чистом сервере план не соберётся."""
    text = read_infra_text()
    targets = set(re.findall(r"^\s*-target=(\S+)", (SCRIPTS / "infra.sh").read_text(), re.MULTILINE))
    for address in deploy_report.STATEFUL:
        name = address.split(".", 1)[1]
        body = re.search(rf'^resource\s+"docker_container"\s+"{name}"\s*\{{\n(.*?)^\}}', text, re.MULTILINE | re.DOTALL)
        assert body, address
        networks = {f"docker_network.{n}" for n in re.findall(r"name\s*=\s*docker_network\.(\w+)\.name", body.group(1))}
        assert networks and networks <= targets, (address, networks - targets)
    assert "docker_network.dmc268" in targets


def test_every_volume_prevents_destroy() -> None:
    """Сторож в plan.sh не видит targeted apply шага «Поднять базу и брокер»: том там держит prevent_destroy."""
    blocks = volume_blocks()
    assert blocks, "ресурсы docker_volume не найдены в infra/*.tf"
    for address in blocks:
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


def test_failed_migrations_take_the_database_revision_from_alembic_current() -> None:
    """Сценарий #213: что откатилось, говорит база, а не лог — `alembic current` после сбоя."""
    # Одна транзакция: упавшая 0014 откатила и 0013, база на 0012.
    assert deploy_report.migrations(FAILED_MIGRATION_LOG, "failure", "0012_b") == {
        "applied": 0, "from": "0012_b", "failed": "0014_d", "current": "0012_b",
    }
    # autocommit_block зафиксировал 0013 до падения 0014.
    assert deploy_report.migrations(FAILED_MIGRATION_LOG, "failure", "0013_c")["applied"] == 1
    # Commit прошёл, код выхода потерян: база уже на 0014.
    assert deploy_report.migrations(FAILED_MIGRATION_LOG, "failure", "0014_d")["applied"] == 2
    # Ревизию базы узнать не удалось — и число применённых неизвестно.
    assert deploy_report.migrations(FAILED_MIGRATION_LOG, "failure", None) == {
        "applied": None, "from": "0012_b", "failed": "0014_d", "current": None,
    }


def test_alembic_current_output_is_parsed() -> None:
    assert deploy_report.current_revision("0012 (head)\n") == "0012"
    assert deploy_report.current_revision("0013_c\n") == "0013_c"
    # Пустая база: alembic current ничего не печатает.
    assert deploy_report.current_revision("\n") == ""


def failed_migrate_summary(tmp_path: Path, *, log: str = FAILED_MIGRATION_LOG, current: str | None) -> str:
    report = tmp_path / "report"
    report.mkdir()
    (report / "migrate.log").write_text(log)
    if current is None:
        (report / "migrate-current.log").write_text("")
        (report / "migrate-current.error").write_text("255\n")
    else:
        (report / "migrate-current.log").write_text(current + "\n")
    results = ALL_SUCCESS.replace("migrate success success", "migrate failure failure")
    return summary(tmp_path, report, JOB_STATUS="failure", STEP_RESULTS=results)


def test_summary_of_rolled_back_migrations(tmp_path: Path) -> None:
    text = failed_migrate_summary(tmp_path, current="0012_b (head)")

    assert "| Применено ревизий | `dmc268.deploy.migrations.applied` | 0 |" in text
    assert "| Упала на ревизии | `dmc268.deploy.migrations.revision.failed` | `0014_d` |" in text
    assert "| Ревизия базы | `dmc268.deploy.migrations.revision.current` | `0012_b` |" in text
    assert "транзакция миграций откатена" in text
    # Ревью #220: DDL из autocommit_block упавшей ревизии мог остаться — оговорка в тексте.
    assert "`autocommit_block`" in text
    assert "dmc268.deploy.migrations.revision.to" not in text


def test_summary_of_failed_migrations_when_the_database_did_not_answer(tmp_path: Path) -> None:
    """Сценарий ревью #220: ssh оборвался — сирота мог довести миграции до commit, «откатено» не пишем."""
    text = failed_migrate_summary(tmp_path, current=None)

    assert "упала на `0014_d`, ревизия базы неизвестна — проверьте `alembic current`" in text
    assert "откат" not in text
    assert "| Применено ревизий | `dmc268.deploy.migrations.applied` | неизвестно |" in text
    assert "| Применено ревизий | `dmc268.deploy.migrations.applied` | 0 |" not in text


def test_summary_of_failed_migrations_that_committed_part(tmp_path: Path) -> None:
    text = failed_migrate_summary(tmp_path, current="0013_c")

    assert "| Применено ревизий | `dmc268.deploy.migrations.applied` | 1 |" in text
    assert "откат" not in text
    assert "`0013_c`" in text


def test_summary_of_migrations_that_failed_before_the_first_revision(tmp_path: Path) -> None:
    text = failed_migrate_summary(tmp_path, log="docker: Error response from daemon: Conflict.\n", current="0012_b")

    assert "| Применено ревизий | `dmc268.deploy.migrations.applied` | 0 |" in text
    assert "до первой ревизии" in text


def test_summary_of_a_migrate_step_that_failed_before_alembic(tmp_path: Path) -> None:
    """Сценарий ревью #220: `tofu output` или pull образа упали — лога alembic нет, но шаг шёл."""
    results = ALL_SUCCESS.replace("migrate success success", "migrate failure failure")

    text = summary(tmp_path, tmp_path / "empty", JOB_STATUS="failure", STEP_RESULTS=results)

    assert "Шаг миграций упал до alembic (`tofu output` или pull образа): схема не менялась." in text
    assert "Шаг миграций не запускался." not in text


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


def test_migrate_asks_the_database_revision_after_a_failure(tmp_path: Path) -> None:
    (tmp_path / "alembic.log").write_text(FAILED_MIGRATION_LOG)
    result, report, _ = run_script(
        tmp_path, "migrate.sh",
        API_IMAGE="ghcr.io/o/r:t@sha256:" + "0" * 64, DOCKER_HOST="ssh://stand",
        MIGRATE_LOG=str(tmp_path / "alembic.log"), MIGRATE_EXIT="1", CURRENT="0012_b (head)",
    )

    assert result.returncode == 1
    calls = (tmp_path / "calls").read_text().splitlines()
    assert [c for c in calls if c.startswith("docker run ") and "alembic current" in c], calls
    # Под timeout: при оборванном ssh вопрос к базе не держит шаг.
    assert any(c.startswith("timeout 60 docker run ") and "alembic current" in c for c in calls), calls
    assert (report / "migrate-current.log").read_text().strip() == "0012_b (head)"
    assert not (report / "migrate-current.error").exists()


def test_migrate_records_that_the_database_revision_is_unknown(tmp_path: Path) -> None:
    (tmp_path / "alembic.log").write_text(FAILED_MIGRATION_LOG)
    result, report, _ = run_script(
        tmp_path, "migrate.sh",
        API_IMAGE="ghcr.io/o/r:t@sha256:" + "0" * 64, DOCKER_HOST="ssh://stand",
        MIGRATE_LOG=str(tmp_path / "alembic.log"), MIGRATE_EXIT="1", CURRENT_FAIL="1",
    )

    assert result.returncode == 1
    assert (report / "migrate-current.error").read_text().strip() == "255"


def test_migrate_does_not_ask_after_a_success(tmp_path: Path) -> None:
    result, report, _ = run_script(
        tmp_path, "migrate.sh",
        API_IMAGE="ghcr.io/o/r:t@sha256:" + "0" * 64, DOCKER_HOST="ssh://stand",
        MIGRATE_LOG=str(tmp_path / "alembic.log"),
    )

    assert result.returncode == 0
    assert "alembic current" not in (tmp_path / "calls").read_text()
    assert not (report / "migrate-current.log").exists()


def test_every_migrations_container_is_restricted() -> None:
    """И `alembic current` после сбоя идёт с теми же ограничениями, что upgrade."""
    script = (SCRIPTS / "migrate.sh").read_text()
    runs = re.findall(r"^\s*(?:timeout \d+ )?docker run --rm\b(?:[^\n]*\\\n)*[^\n]*$", commands(script), re.MULTILINE)
    assert len(runs) == 2, runs
    for run in runs:
        assert not missing_migration_restrictions(run.strip().removeprefix("timeout 60 ")), run


def test_every_migrations_run_takes_the_shared_arguments() -> None:
    """Ревью #220: сеть и ограничения — в одном массиве, а не копией у каждого запуска.

    Копия разошлась: после #102 `alembic current` остался в сети `dmc268`.
    Строка `docker run` в скрипте одна — в массиве; запуски — через него.
    """
    script = (SCRIPTS / "migrate.sh").read_text()
    lines = [line for line in script.splitlines() if not line.lstrip().startswith("#")]
    assert len([line for line in lines if re.search(r"(?<!\S)docker run\b", line)]) == 1, lines
    assert len([line for line in lines if '"${migrations_run[@]}"' in line]) == 2, lines


def test_migrate_keeps_the_alembic_exit_code(tmp_path: Path) -> None:
    (tmp_path / "alembic.log").write_text(FAILED_MIGRATION_LOG)
    result, report, _ = run_script(
        tmp_path, "migrate.sh",
        API_IMAGE="ghcr.io/o/r:t@sha256:" + "0" * 64, DOCKER_HOST="ssh://stand",
        MIGRATE_LOG=str(tmp_path / "alembic.log"), MIGRATE_EXIT="1",
    )

    assert result.returncode == 1
    assert "0014_d" in (report / "migrate.log").read_text()


# Вывод alembic, когда миграция не дождалась блокировки (lock_timeout 5 с в
# alembic/env.py): так падает upgrade на живом PostgreSQL за удержанной
# ACCESS EXCLUSIVE (tests/db/test_alembic_env.py).
LOCKED_MIGRATION_LOG = (
    "INFO  [alembic.runtime.migration] Will assume transactional DDL.\n"
    "INFO  [alembic.runtime.migration] Running upgrade 0012_b -> 0013_c, таблица\n"
    "INFO  [alembic.runtime.migration] Running upgrade 0013_c -> 0014_d, индекс\n"
    "Traceback (most recent call last):\n"
    "sqlalchemy.exc.OperationalError: (psycopg.errors.LockNotAvailable) canceling statement due to lock timeout\n"
)
APPLIED_LOG = (
    "INFO  [alembic.runtime.migration] Running upgrade 0012_b -> 0013_c, таблица\n"
    "INFO  [alembic.runtime.migration] Running upgrade 0013_c -> 0014_d, индекс\n"
)


def test_lock_timeout_is_retried_only_when_the_whole_transaction_rolled_back() -> None:
    """Ревью #220: повтор безопасен, только если база на ревизии, с которой попытка начала."""
    assert deploy_report.lock_retry(LOCKED_MIGRATION_LOG, "0012_b")
    # SQLSTATE вместо имени класса — тот же сбой.
    assert deploy_report.lock_retry(LOCKED_MIGRATION_LOG.replace("LockNotAvailable", "55P03"), "0012_b")
    # Пустая база: попытка начала с пустой ревизии.
    assert deploy_report.lock_retry(LOCKED_MIGRATION_LOG.replace("0012_b -> ", " -> "), "")
    # autocommit_block зафиксировал 0013: повтор начнёт уже с другой ревизии — решает человек.
    assert not deploy_report.lock_retry(LOCKED_MIGRATION_LOG, "0013_c")
    # Ревизия базы неизвестна — повтора нет.
    assert not deploy_report.lock_retry(LOCKED_MIGRATION_LOG, None)
    # Другая ошибка — не блокировка.
    assert not deploy_report.lock_retry(FAILED_MIGRATION_LOG, "0012_b")
    # statement_timeout (57014) — не повод повторять: запрос долгий сам по себе.
    assert not deploy_report.lock_retry(
        LOCKED_MIGRATION_LOG.replace("LockNotAvailable) canceling statement due to lock timeout",
                                     "QueryCanceled) canceling statement due to statement timeout"),
        "0012_b",
    )
    # Ни одна ревизия не начата — не с чем сверить базу.
    assert not deploy_report.lock_retry("sqlalchemy.exc.OperationalError: (psycopg.errors.LockNotAvailable)\n", "")


def migrate(tmp_path: Path, **env: str) -> tuple[subprocess.CompletedProcess[str], Path, list[str]]:
    """migrate.sh с подложным docker; вернуть результат, каталог отчёта и запуски upgrade."""
    for name, text in list(env.items()):
        if name.startswith("MIGRATE_LOG") and not text.startswith("/"):
            (tmp_path / name).write_text(text)
            env[name] = str(tmp_path / name)
    result, report, _ = run_script(
        tmp_path, "migrate.sh",
        API_IMAGE="ghcr.io/o/r:t@sha256:" + "0" * 64, DOCKER_HOST="ssh://stand", MIGRATE_RETRY_PAUSE="0",
        **env,
    )
    calls = (tmp_path / "calls").read_text().splitlines()
    return result, report, [c for c in calls if c.startswith("docker run ") and "alembic upgrade head" in c]


def test_migrate_retries_a_lock_timeout_that_rolled_back(tmp_path: Path) -> None:
    """Автодеплой за длинной транзакцией воркера: вторая попытка проходит, шаг зелёный."""
    result, report, upgrades = migrate(
        tmp_path, MIGRATE_LOG_1=LOCKED_MIGRATION_LOG, MIGRATE_EXIT_1="1", MIGRATE_LOG_2=APPLIED_LOG,
        CURRENT="0012_b",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert len(upgrades) == 2
    assert "::warning::миграции не дождались блокировки" in result.stdout
    assert (report / "migrate-attempts").read_text().strip() == "2"
    # В отчёте — лог удачной попытки, а не упавшей.
    assert "LockNotAvailable" not in (report / "migrate.log").read_text()

    results = ALL_SUCCESS
    text = summary(tmp_path, report, JOB_STATUS="success", STEP_RESULTS=results)
    assert "| Попыток | `dmc268.deploy.migrations.attempts` | 2 |" in text
    assert "| Применено ревизий | `dmc268.deploy.migrations.applied` | 2 |" in text


def test_migrate_gives_up_after_three_attempts(tmp_path: Path) -> None:
    result, report, upgrades = migrate(tmp_path, MIGRATE_LOG=LOCKED_MIGRATION_LOG, MIGRATE_EXIT="1", CURRENT="0012_b")

    assert result.returncode == 1
    assert len(upgrades) == 3
    assert (report / "migrate-attempts").read_text().strip() == "3"


def test_migrate_does_not_retry_when_part_was_committed(tmp_path: Path) -> None:
    result, _, upgrades = migrate(tmp_path, MIGRATE_LOG=LOCKED_MIGRATION_LOG, MIGRATE_EXIT="1", CURRENT="0013_c")

    assert result.returncode == 1
    assert len(upgrades) == 1


def test_migrate_does_not_retry_when_the_database_did_not_answer(tmp_path: Path) -> None:
    result, report, upgrades = migrate(tmp_path, MIGRATE_LOG=LOCKED_MIGRATION_LOG, MIGRATE_EXIT="1", CURRENT_FAIL="1")

    assert result.returncode == 1
    assert len(upgrades) == 1
    assert (report / "migrate-current.error").exists()


def test_migrate_does_not_retry_other_failures(tmp_path: Path) -> None:
    result, _, upgrades = migrate(tmp_path, MIGRATE_LOG=FAILED_MIGRATION_LOG, MIGRATE_EXIT="1", CURRENT="0012_b")

    assert result.returncode == 1
    assert len(upgrades) == 1


def test_migrate_does_not_start_a_retry_past_its_deadline(tmp_path: Path) -> None:
    """Повтор, начатый у самого потолка шага, раннер убил бы посреди миграции."""
    result, _, upgrades = migrate(
        tmp_path, MIGRATE_LOG=LOCKED_MIGRATION_LOG, MIGRATE_EXIT="1", CURRENT="0012_b", MIGRATE_RETRY_DEADLINE="0",
    )

    assert result.returncode == 1
    assert len(upgrades) == 1


def test_a_stale_unknown_revision_does_not_outlive_a_retry(tmp_path: Path) -> None:
    """Ответ базы — от последней попытки: `.error` прошлой не остаётся рядом с новым ответом."""
    report = tmp_path / "report"
    report.mkdir()
    (report / "migrate-current.error").write_text("255\n")
    result, report, _ = migrate(tmp_path, MIGRATE_LOG=FAILED_MIGRATION_LOG, MIGRATE_EXIT="1", CURRENT="0012_b")

    assert result.returncode == 1
    assert not (report / "migrate-current.error").exists()
    assert (report / "migrate-current.log").read_text().strip() == "0012_b"


def test_migrate_retries_fit_the_step_timeout() -> None:
    """Последний повтор начинается не позже срока, а после него остаётся 5 мин: два
    запроса по statement_timeout (2 мин) и `alembic current` под `timeout 60`."""
    script = (SCRIPTS / "migrate.sh").read_text()
    deadline = re.search(r'MIGRATE_RETRY_DEADLINE:-(\d+)', script)
    pause = re.search(r'MIGRATE_RETRY_PAUSE:-(\d+)', script)
    assert deadline and pause, "нет срока или паузы повтора в migrate.sh"
    migrate_step = next(s for s in deploy_steps() if s.get("id") == "migrate")
    assert int(deadline.group(1)) + 5 * 60 <= int(migrate_step["timeout-minutes"]) * 60  # type: ignore[call-overload]
    assert 0 < int(pause.group(1)) < 60


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


def test_summary_removes_a_targeted_plan_left_by_a_killed_step(tmp_path: Path) -> None:
    """Ревью #220: шаг «Поднять базу и брокер» отменён или убит по таймауту — trap infra.sh
    не успел, а `infra/infra.tfplan` с паролями остался на раннере."""
    report = tmp_path / "report"
    report.mkdir()
    root = tmp_path / "repo"
    (root / "infra").mkdir(parents=True)
    (root / "infra" / "infra.tfplan").write_text("пароли")

    summary(tmp_path, report, JOB_STATUS="cancelled", STEP_RESULTS=ALL_SUCCESS)

    assert not (root / "infra" / "infra.tfplan").exists()


def test_targeted_plan_and_apply_wait_for_a_short_foreign_lock() -> None:
    """Как plan.sh и apply.sh в #215: чужая короткая блокировка не роняет деплой сразу."""
    script = commands((SCRIPTS / "infra.sh").read_text())
    for verb in ("plan", "apply"):
        command = re.search(rf"^tofu -chdir=infra {verb}\b(?:[^\n]*\\\n)*[^\n]*$", script, re.MULTILINE)
        assert command, verb
        assert "-lock-timeout=2m" in command.group(0), verb


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
