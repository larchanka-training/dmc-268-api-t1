"""Отчёт деплоя: сторож плана OpenTofu и summary job'а `deploy`.

Две команды, обе — стандартная библиотека, системный `python3` раннера (3.12 на
ubuntu-latest), как у `check_openspec_archived.py`:

    tofu -chdir=infra show -json tfplan | python3 scripts/deploy_report.py plan
    python3 scripts/deploy_report.py render >> "$GITHUB_STEP_SUMMARY"

`plan` читает план со stdin, пишет счётчики изменений в `plan.json` и падает,
если план уничтожает том базы или брокера (спека `backend-delivery`,
«Развёртывание не уничтожает тома данных»). Сам план на диск не пишется: в
`tofu show -json` значения переменных и атрибутов, пароли тоже, лежат открытым
текстом.

`render` собирает summary из того, что шаги деплоя сложили в каталог отчёта
(`REPORT_DIR`, по умолчанию `$RUNNER_TEMP/deploy-report`):

- `marks` — засечки начала шагов (`.github/scripts/deploy/mark.sh`);
- `plan.json` — от команды `plan`;
- `api-old-id`, `api-events.jsonl`, `api-new.json` — контейнер API до apply,
  события Docker во время apply и состояние нового контейнера (`apply.sh`);
- `migrate.log` — вывод `alembic upgrade head` (`migrate.sh`);
- `disk.txt` — `df -Pk` корня и каталога Docker (`summary.sh`).

Нет файла — нет и раздела, а не падение: summary пишется и после упавшего шага.

Ключи значений — по семантическим соглашениям OpenTelemetry (CI/CD, container,
system.filesystem), чтобы отправить их в коллектор без переименований. Своим
значениям, которых в соглашениях нет, — пространство `dmc268.deploy.*`:
соглашения велят не занимать чужие пространства имён и выделять своё
(«Attribute Naming», opentelemetry.io/docs/specs/semconv/general/naming/).
"""

import itertools
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# Тома с данными: их уничтожение — потеря базы или очередей. Замена тома —
# тоже уничтожение (delete, create).
PROTECTED = ("docker_volume.postgres", "docker_volume.rabbitmq")

# Действия плана (`resource_changes[].change.actions` у OpenTofu 1.13.2) —
# в колонки, как в строке `Plan:` у tofu, но замена отдельно, а не в add и
# destroy сразу. `no-op` и `read` — не изменения.
ACTIONS = {
    ("create",): "add",
    ("update",): "change",
    ("delete",): "destroy",
    ("delete", "create"): "replace",
    ("create", "delete"): "replace",
    ("forget",): "forget",
}
COLUMNS = ("add", "change", "destroy", "replace", "forget")
UNCHANGED = (("no-op",), ("read",))

# Шаги job'а `deploy` по порядку: id, подпись, id шагов группы. Подпись —
# `name` шага в deploy.yml (сверяет tests/test_deploy_report.py). Шаги с
# `uses` засечку не ставят, их время входит в предыдущую засечку: так все они
# собраны в «Подготовку» до первого шага со скриптом.
STEPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("start", "Подготовка: checkout, SSH, OpenTofu, хранилище состояния, вход в ghcr",
     ("start", "checkout", "ssh", "tofu", "s3", "login")),
    ("diagnostics", "Доставки вебхука и сбои воркера с прошлого выката", ("diagnostics",)),
    ("infra", "Поднять базу и брокер", ("infra",)),
    ("migrate", "Применить миграции", ("migrate",)),
    ("plan", "Спланировать и проверить план", ("plan",)),
    ("apply", "Применить конфигурацию", ("apply",)),
    ("api_logs", "Логи API после упавшего apply", ("api_logs",)),
    ("check_api", "Проверить, что API жив (через туннель)", ("check_api",)),
    ("check_worker", "Проверить, что воркер работает", ("check_worker",)),
    ("prune", "Убрать устаревшие образы", ("prune",)),
)
# Засечка шага summary закрывает последний шаг и весь job.
SUMMARY_MARK = "summary"

# `job.status` и `steps.*.conclusion` — в значения `cicd.pipeline.task.run.result`.
RESULTS = {
    "success": "success",
    "failure": "failure",
    "cancelled": "cancellation",
    "skipped": "skip",
}

# Сколько записей держит State.Health.Log у Docker: старые вытесняются.
HEALTH_LOG_SIZE = 5


# --- план -----------------------------------------------------------------


def plan_changes(plan: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Счётчики изменений плана: тип ресурса → действие → число ресурсов."""
    changes: dict[str, dict[str, int]] = {}
    for rc in plan.get("resource_changes", []):
        actions = tuple(rc["change"]["actions"])
        if actions in UNCHANGED:
            continue
        action = ACTIONS.get(actions, "/".join(actions))
        by_type = changes.setdefault(rc["type"], {})
        by_type[action] = by_type.get(action, 0) + 1
    return changes


def protected_deletes(plan: dict[str, Any]) -> list[str]:
    """Защищённые ресурсы, которые план уничтожает или заменяет."""
    return [
        rc["address"]
        for rc in plan.get("resource_changes", [])
        if rc["address"] in PROTECTED and "delete" in rc["change"]["actions"]
    ]


def plan_command(report_dir: Path) -> int:
    plan = json.load(sys.stdin)
    changes = plan_changes(plan)
    deleted = protected_deletes(plan)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "plan.json").write_text(
        json.dumps({"changes": changes, "protected_deletes": deleted}, ensure_ascii=False)
    )
    print("\n".join(plan_table(changes)) if changes else "План без изменений.")
    if deleted:
        for address in deleted:
            print(
                f"::error::план уничтожает том данных {address}: apply не запускается. "
                "Сменить том — новым ресурсом, а прежний снять с учёта блоком "
                "removed { lifecycle { destroy = false } }; данные останутся на сервере."
            )
        return 1
    print("Тома данных план не уничтожает.")
    return 0


def plan_table(changes: dict[str, dict[str, int]]) -> list[str]:
    extra = sorted({a for by_type in changes.values() for a in by_type} - set(COLUMNS))
    columns = (*COLUMNS, *extra)
    lines = [
        "| Тип ресурса | " + " | ".join(f"`{c}`" for c in columns) + " |",
        "| --- |" + " ---: |" * len(columns),
    ]
    for rtype in sorted(changes):
        cells = (str(changes[rtype].get(c, "")) for c in columns)
        lines.append(f"| `{rtype}` | " + " | ".join(cells) + " |")
    return lines


# --- простой API ----------------------------------------------------------


def parse_time(value: str) -> float:
    """RFC 3339 из Docker в секунды эпохи. Наносекунды отбрасываются до микро."""
    return datetime.fromisoformat(value).timestamp()


def api_downtime(old_id: str, events: list[dict[str, Any]], new: dict[str, Any] | None) -> dict[str, Any]:
    """Разбивка простоя API при пересоздании контейнера.

    Время остановки старого — из событий Docker: контейнер к концу apply уже
    удалён, inspect его не покажет. Начало простоя — SIGTERM (`kill` с
    сигналом 15): по нему uvicorn первым делом закрывает слушающий сокет, и
    новые соединения старый API уже не принимает.
    Время нового — из `docker inspect`; готовность — конец первой успешной
    проверки в `State.Health.Log`. Все отметки — с часов сервера.
    """
    if new is None:
        return {"status": "missing"}
    if not old_id:
        return {"status": "created"}
    if new["Id"] == old_id:
        return {"status": "kept"}

    own = [e for e in events if e.get("Actor", {}).get("ID") == old_id]

    def first(action: str, **attributes: str) -> float | None:
        for e in own:
            attrs = e.get("Actor", {}).get("Attributes", {})
            if e.get("Action") == action and all(attrs.get(k) == v for k, v in attributes.items()):
                return int(e["timeNano"]) / 1e9
        return None

    term = first("kill", signal="15")
    died = first("die")
    created = parse_time(new["Created"])
    started_at = new["State"].get("StartedAt", "")
    started = parse_time(started_at) if not started_at.startswith("0001-") else None

    log = (new["State"].get("Health") or {}).get("Log") or []
    success = next((i for i, entry in enumerate(log) if entry["ExitCode"] == 0), None)
    healthy = parse_time(log[success]["End"]) if success is not None else None

    def span(a: float | None, b: float | None) -> float | None:
        return round(b - a, 3) if a is not None and b is not None else None

    return {
        "status": "replaced",
        "stop": span(term, died),
        "recreate": span(died, created),
        "start": span(created, started),
        "ready": span(started, healthy),
        "total": span(term, healthy),
        "failed_checks": success if success is not None else len(log),
        # Docker держит пять последних записей: в полном журнале более ранние
        # неуспешные проверки могли вытесниться, и число — нижняя граница.
        "failed_checks_at_least": len(log) >= HEALTH_LOG_SIZE,
        "check": span(parse_time(log[success]["Start"]), healthy) if success is not None else None,
    }


# --- миграции -------------------------------------------------------------

UPGRADE = re.compile(r"Running upgrade (\S*) -> ([^\s,]+)")


def migrations(log: str) -> dict[str, Any]:
    """Применённые ревизии из вывода `alembic upgrade head`."""
    steps = UPGRADE.findall(log)
    if not steps:
        return {"applied": 0}
    return {"applied": len(steps), "from": steps[0][0] or "пустая база", "to": steps[-1][1]}


# --- диск -----------------------------------------------------------------


def disk(text: str) -> list[dict[str, Any]]:
    """Файловые системы из `df -Pk / <каталог Docker>`.

    Перед выводом df — строка `docker_root <путь>`. df печатает по строке на
    аргумент в их порядке: первая — корень, вторая — файловая система каталога
    Docker. Если это одна файловая система, строка одна.
    """
    docker_root = ""
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("docker_root "):
            docker_root = line.split(" ", 1)[1].strip()
            continue
        fields = line.split()
        if len(fields) < 6 or not fields[2].isdigit() or not fields[3].isdigit():
            continue
        used, avail = int(fields[2]) * 1024, int(fields[3]) * 1024
        rows.append({
            "mountpoint": " ".join(fields[5:]),
            "free": avail,
            "utilization": used / (used + avail) if used + avail else 0.0,
            "what": ["корень"] if not rows else [],
        })
    if len(rows) >= 2 and docker_root:
        rows[1]["what"].append(f"Docker (`{docker_root}`)")
        if rows[1]["mountpoint"] == rows[0]["mountpoint"]:
            rows[0]["what"] += rows.pop(1)["what"]
    return rows[:2]


def human_bytes(n: float) -> str:
    for unit in ("Б", "КиБ", "МиБ", "ГиБ"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ТиБ"


# --- шаги -----------------------------------------------------------------


def read_marks(text: str) -> dict[str, float]:
    """Засечки `id время`; у повторной засечки того же шага берётся первая."""
    marks: dict[str, float] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                marks.setdefault(parts[0], float(parts[1]))
            except ValueError:
                continue
    return marks


def durations(marks: dict[str, float]) -> dict[str, float]:
    """Длительность шага — до следующей по времени засечки."""
    ordered = sorted(marks.items(), key=lambda kv: kv[1])
    return {a: round(tb - ta, 1) for (a, ta), (_, tb) in itertools.pairwise(ordered)}


def read_step_results(text: str) -> dict[str, dict[str, str]]:
    """`STEP_RESULTS` из deploy.yml: строки `id outcome conclusion`.

    У шага, до которого job не дошёл (отменён раньше), исход пустой — такой
    шаг пропускается.
    """
    results = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 3:
            results[parts[0]] = {"outcome": parts[1], "conclusion": parts[2]}
    return results


def step_rows(steps: dict[str, dict[str, str]], marks: dict[str, float]) -> list[tuple[str, str, float | None]]:
    """Строки таблицы шагов: подпись, результат, длительность."""
    took = durations(marks)
    rows = []
    for mark, label, ids in STEPS:
        found = [steps[i] for i in ids if i in steps]
        if not found:
            continue
        conclusions = {s["conclusion"] for s in found}
        if bad := conclusions & {"failure", "cancelled"}:
            result = RESULTS[min(bad)]
        elif conclusions == {"skipped"}:
            result = RESULTS["skipped"]
        elif any(s["outcome"] == "failure" for s in found):
            # continue-on-error: шаг упал, а job продолжился.
            result = "failure, не роняет деплой"
        else:
            result = RESULTS["success"]
        rows.append((label, result, took.get(mark)))
    return rows


def failed_step(steps: dict[str, dict[str, str]]) -> tuple[str, str] | None:
    """Первый шаг, уронивший или прервавший job: id и подпись."""
    for _, label, ids in STEPS:
        for i in ids:
            if steps.get(i, {}).get("conclusion") in ("failure", "cancelled"):
                return i, label
    return None


# --- summary --------------------------------------------------------------


def seconds(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "—"


def image_parts(image: str) -> tuple[str, str, str]:
    """Имя, тег и digest из `<репозиторий>:<тег>@sha256:<digest>`."""
    match = re.fullmatch(r"(.+?)(?::([\w][\w.-]{0,127}))?@(sha256:[0-9a-f]{64})", image)
    if not match:
        return image, "", ""
    return match.group(1), match.group(2) or "", match.group(3)


def read(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def read_events(text: str) -> list[dict[str, Any]]:
    events = []
    for line in text.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def render(report_dir: Path, env: dict[str, str]) -> str:
    steps = read_step_results(env.get("STEP_RESULTS", ""))
    marks = read_marks(read(report_dir / "marks"))
    status = env.get("JOB_STATUS", "")
    result = RESULTS.get(status, status)
    failed = failed_step(steps)
    run_url = f"{env.get('GITHUB_SERVER_URL', '')}/{env.get('GITHUB_REPOSITORY', '')}/actions/runs/{env.get('GITHUB_RUN_ID', '')}"
    name, tag, digest = image_parts(env.get("IMAGE", ""))

    headline = {"success": "успешно", "failure": "упал", "cancellation": "отменён"}.get(result, result)
    if failed and result != "success":
        headline += f" на шаге «{failed[1]}»"
    out = [f"## Деплой: {headline}", "", "| | Ключ | Значение |", "| --- | --- | --- |"]
    out.append(f"| Исход | `cicd.pipeline.task.run.result` | `{result}` |")
    if failed and result != "success":
        out.append(f"| Шаг падения | `error.type` | `{failed[0]}` — {failed[1]} |")
    out += [
        f"| Workflow | `cicd.pipeline.name` | {env.get('GITHUB_WORKFLOW', '')} |",
        f"| Прогон | `cicd.pipeline.run.id` | [{env.get('GITHUB_RUN_ID', '')}]({run_url}) |",
        f"| Job | `cicd.pipeline.task.name` | `{env.get('GITHUB_JOB', '')}` (`cicd.pipeline.task.type`: `deploy`) |",
        "| Окружение | `deployment.environment.name` | `stand` |",
        f"| Коммит | `vcs.ref.head.revision` | `{env.get('SHA', '')}` |",
        f"| Образ | `container.image.name` | `{name}` |",
    ]
    if tag:
        out.append(f"| Тег | `container.image.tags` | `{tag}` |")
    if digest:
        out.append(f"| Digest | `container.image.repo_digests` | `{name}@{digest}` |")
    if env.get("HOST"):
        out.append(f"| Адрес | | https://{env['HOST']}/api/v1/ |")
    if "start" in marks and SUMMARY_MARK in marks:
        out.append(
            f"| Длительность job'а, с | `dmc268.deploy.duration` | {seconds(marks[SUMMARY_MARK] - marks['start'])} |"
        )

    out += ["", *downtime_section(report_dir), "", *plan_section(report_dir)]
    out += ["", *migrations_section(report_dir, marks), "", *disk_section(report_dir)]
    out += ["", *steps_section(steps, marks)]
    return "\n".join(out) + "\n"


def downtime_section(report_dir: Path) -> list[str]:
    out = ["### Простой API", ""]
    if not (report_dir / "api-old-id").exists():
        return [*out, "Apply не запускался."]
    new = read_json(report_dir / "api-new.json")
    d = api_downtime(
        read(report_dir / "api-old-id").strip(),
        read_events(read(report_dir / "api-events.jsonl")),
        new[0] if isinstance(new, list) and new else new if isinstance(new, dict) else None,
    )
    if d["status"] == "kept":
        return [*out, "API не пересоздавался, простоя нет."]
    if d["status"] == "created":
        return [*out, "API создан впервые: прежнего контейнера не было, простой не считается."]
    if d["status"] == "missing":
        return [*out, "Контейнера API после apply нет: простой не посчитать."]
    out += [
        "| Этап | Ключ | с |",
        "| --- | --- | ---: |",
        f"| Остановка старого: SIGTERM → выход процесса | `dmc268.deploy.api.downtime.stop` | {seconds(d['stop'])} |",
        f"| Удаление старого и создание нового → `Created` | `dmc268.deploy.api.downtime.recreate` | {seconds(d['recreate'])} |",
        f"| Старт процесса: `Created` → `State.StartedAt` | `dmc268.deploy.api.downtime.start` | {seconds(d['start'])} |",
        f"| До первой успешной проверки: `StartedAt` → `Health.Log[].End` | `dmc268.deploy.api.downtime.ready` | {seconds(d['ready'])} |",
        f"| **Простой: SIGTERM старому → первая успешная проверка нового** | `dmc268.deploy.api.downtime` | **{seconds(d['total'])}** |",
        "",
    ]
    at_least = "не меньше " if d["failed_checks_at_least"] else ""
    note = f"Неуспешных проверок до первой успешной: {at_least}{d['failed_checks']} (`dmc268.deploy.api.health.failed_checks`)"
    if d["check"] is not None:
        note += f"; сама успешная проверка шла {seconds(d['check'])} с."
    else:
        note += "; успешной проверки не было."
    if d["stop"] is None:
        note += " Событий остановки старого контейнера нет: начало простоя неизвестно."
    return [*out, note]


def plan_section(report_dir: Path) -> list[str]:
    out = ["### План OpenTofu", ""]
    plan = read_json(report_dir / "plan.json")
    if plan is None:
        return [*out, "Плана нет: шаг планирования не дошёл до него."]
    if plan["changes"]:
        out += plan_table(plan["changes"])
        out += ["", ("Ключ — `dmc268.deploy.plan.changes`, атрибуты `dmc268.deploy.plan.resource.type` "
                "(строки) и `dmc268.deploy.plan.action` (колонки). `replace` в `add` и `destroy` не входит.")]
    else:
        out.append("План без изменений.")
    if plan["protected_deletes"]:
        out += ["", "> [!CAUTION]", "> Сторож томов остановил деплой до apply: план уничтожает "
                + ", ".join(f"`{a}`" for a in plan["protected_deletes"])
                + " (`dmc268.deploy.plan.protected_deletes`)."]
    else:
        out += ["", "Тома данных (`" + "`, `".join(PROTECTED) + "`) план не уничтожает."]
    return out


def migrations_section(report_dir: Path, marks: dict[str, float]) -> list[str]:
    out = ["### Миграции", ""]
    if not (report_dir / "migrate.log").exists():
        return [*out, "Шаг миграций не запускался."]
    m = migrations(read(report_dir / "migrate.log"))
    out += ["| | Ключ | Значение |", "| --- | --- | --- |",
            f"| Применено ревизий | `dmc268.deploy.migrations.applied` | {m['applied']} |"]
    if m["applied"]:
        out += [f"| Ревизия до | `dmc268.deploy.migrations.revision.from` | `{m['from']}` |",
                f"| Ревизия после | `dmc268.deploy.migrations.revision.to` | `{m['to']}` |"]
    out.append(f"| Время шага, с | | {seconds(durations(marks).get('migrate'))} |")
    return out


def disk_section(report_dir: Path) -> list[str]:
    out = ["### Диск сервера", ""]
    rows = disk(read(report_dir / "disk.txt"))
    if not rows:
        return [*out, "Свободное место не получено."]
    out += [
        ("| `system.filesystem.mountpoint` | Что на ней | Свободно (`system.filesystem.usage`, `state=free`) "
        "| Занято (`system.filesystem.utilization`) |"),
        "| --- | --- | ---: | ---: |",
    ]
    for r in rows:
        out.append(f"| `{r['mountpoint']}` | {', '.join(r['what'])} | {human_bytes(r['free'])} | {r['utilization']:.0%} |")
    return [*out, "", "После уборки образов; до неё, если уборка не запускалась."]


def steps_section(steps: dict[str, dict[str, str]], marks: dict[str, float]) -> list[str]:
    out = ["### Шаги", "", "| Шаг | `cicd.pipeline.task.run.result` | Длительность, с |", "| --- | --- | ---: |"]
    for label, result, took in step_rows(steps, marks):
        out.append(f"| {label} | `{result}` | {seconds(took)} |")
    return [*out, "", ("Длительность — до засечки следующего шага (`.github/scripts/deploy/mark.sh`): "
            "шаги с `uses` входят в предыдущий.")]


def main(argv: list[str]) -> int:
    report_dir = Path(os.environ.get("REPORT_DIR") or Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "deploy-report")
    if argv == ["plan"]:
        return plan_command(report_dir)
    if argv == ["render"]:
        sys.stdout.write(render(report_dir, dict(os.environ)))
        return 0
    print("использование: deploy_report.py plan|render", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
