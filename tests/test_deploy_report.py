"""Отчёт деплоя: сторож томов, разбивка простоя API и summary job'а `deploy`.

Сторож — единственное, что стоит между планом, уничтожающим том базы, и
`tofu apply`; узнавать о его поломке деплоем нельзя. Поэтому `plan.sh` и
`apply.sh` гоняются настоящим bash с подложными `tofu` и `ssh`, а вход —
настоящие данные: `tests/fixtures/deploy/plan-*.json` — урезанный до
`resource_changes` вывод `tofu show -json` OpenTofu 1.13.2 на демоне Docker
29.9.2 (выкат после #204 и план с заменой и удалением томов), `api-*` — то, что
собрал `apply.sh` при таком выкате: события Docker, id старого контейнера и
состояние нового.

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

# Подложный tofu: `plan -out` создаёт файл плана, `show -json` отдаёт
# фикстуру, `apply` падает, если велит тест. Вызовы — в журнал.
FAKE_TOFU = """#!/usr/bin/env bash
echo "$*" >> "$CALLS"
case "$*" in
  *" plan "*) touch infra/tfplan ;;
  *" show -json tfplan") cat "$PLAN_JSON" ;;
  *" apply "*) [ -e infra/tfplan ] || exit 3; exit "${APPLY_EXIT:-0}" ;;
esac
"""

# Подложный ssh: отвечает на команды apply.sh и summary.sh из файлов фикстур.
FAKE_SSH = """#!/usr/bin/env bash
while [ $# -gt 0 ]; do case "$1" in -o) shift 2 ;; -*) shift ;; *) break ;; esac; done
shift
echo "$*" >> "$CALLS"
case "$*" in
  *"docker events"*) cat "$FIX/api-events.jsonl"; exec sleep 30 ;;
  *"docker inspect -f '{{.Id}}'"*) cat "$FIX/api-old-id" ;;
  *"docker inspect -f"*) cat "$FIX/api-new.json" ;;
  *"docker info"*) printf 'docker_root /var/lib/docker\\n%s\\n' "$DF" ;;
esac
"""

GNU_DF = """Filesystem     1024-blocks     Used Available Capacity Mounted on
/dev/vda1         81106868 30553012  50537472      38% /
/dev/vda1         81106868 30553012  50537472      38% /"""


def fake_bin(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, body in (("tofu", FAKE_TOFU), ("ssh", FAKE_SSH)):
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
    assert "Тома данных план не уничтожает." in result.stdout
    assert json.loads((report / "plan.json").read_text()) == {
        "changes": {
            "docker_container": {"replace": 2},
            "docker_image": {"forget": 2},
            "terraform_data": {"change": 1},
        },
        "protected_deletes": [],
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


def test_guard_protects_every_volume_the_stack_declares() -> None:
    """Новый том или переименованный ресурс тома без правки сторожа — дыра в защите."""
    declared = set(re.findall(r'resource\s+"docker_volume"\s+"(\w+)"', read_infra_text()))
    assert declared, "ресурсы docker_volume не найдены в infra/*.tf"
    assert {f"docker_volume.{name}" for name in declared} == set(deploy_report.PROTECTED)


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
    inspects = re.findall(r"^\s*ssh stand \"docker inspect[^\n]*", script, re.MULTILINE)
    assert len(inspects) == 2, inspects
    assert all(" -f " in call for call in inspects), inspects


def load_downtime(**changes: object) -> dict[str, object]:
    new = json.loads((FIXTURES / "api-new.json").read_text())
    new.update(changes)
    return deploy_report.api_downtime(
        (FIXTURES / "api-old-id").read_text().strip(),
        deploy_report.read_events((FIXTURES / "api-events.jsonl").read_text()),
        new,
    )


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
    parts = (d["stop"], d["recreate"], d["start"], d["ready"])
    assert sum(parts) == pytest.approx(d["total"], abs=0.005)  # type: ignore[arg-type]


def test_downtime_when_the_new_api_never_became_healthy() -> None:
    new = json.loads((FIXTURES / "api-new.json").read_text())
    failing = [{**entry, "ExitCode": 1} for entry in new["State"]["Health"]["Log"]] * 2
    d = load_downtime(State={**new["State"], "Health": {"Status": "unhealthy", "Log": failing[:5]}})

    assert d["ready"] is None and d["total"] is None
    assert d["stop"] == pytest.approx(1.14, abs=0.002)
    assert d["failed_checks"] == 5 and d["failed_checks_at_least"]


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
    result = subprocess.run(
        ["bash", str((SCRIPTS / "summary.sh").resolve())],
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
