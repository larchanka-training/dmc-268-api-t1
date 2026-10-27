"""Отчёт деплоя: сторож плана OpenTofu и summary job'а `deploy`.

Три команды, все — стандартная библиотека, системный `python3` раннера (3.12 на
ubuntu-latest), как у `check_openspec_archived.py`:

    tofu -chdir=infra show -json tfplan | python3 scripts/deploy_report.py plan
    tofu -chdir=infra show -json infra.tfplan | python3 scripts/deploy_report.py plan infra
    python3 scripts/deploy_report.py migrate-retry
    python3 scripts/deploy_report.py render >> "$GITHUB_STEP_SUMMARY"

`plan` читает план со stdin, пишет счётчики изменений в `plan.json` (`plan
infra` — targeted-план шага «Поднять базу и брокер», в `plan-infra.json`) и
падает, если план уничтожает том базы или брокера (спека `backend-delivery`,
«Развёртывание не уничтожает тома данных») или пересоздаёт либо удаляет их
контейнер («Развёртывание не пересоздаёт базу и брокер»). Сам план на диск не
пишется: в `tofu show -json` значения переменных и атрибутов, пароли тоже,
лежат открытым текстом.

`migrate-retry` — решение `migrate.sh` после упавшего upgrade: код 0, если
миграция не дождалась блокировки, а база на ревизии, с которой попытка начала
(`lock_retry`).

`render` собирает summary из того, что шаги деплоя сложили в каталог отчёта
(`REPORT_DIR`, по умолчанию `$RUNNER_TEMP/deploy-report`):

- `marks` — засечки начала шагов (`.github/scripts/deploy/mark.sh`);
- `plan-infra.json`, `plan.json` — от команды `plan`;
- `api-old-id`, `api-events.jsonl`, `api-new.json` — контейнер API до apply,
  события Docker во время apply и состояние нового контейнера (`apply.sh`);
  `<файл>.error` рядом — сервер не ответил (код ssh или timeout);
- `migrate.log` — вывод `alembic upgrade head` (`migrate.sh`), после сбоя —
  `migrate-current.log` с выводом `alembic current` (`.error` рядом — база не
  ответила), `migrate-attempts` — номер последней попытки;
  `migrate-monitor.error` — миграции прошли, а пароль роли мониторинга не
  обновлён (код выхода); исход шага миграций — из `STEP_RESULTS`;
- `disk.txt` — `df -Pk` корня и каталога Docker (`summary.sh`);
- `stale-tip` — вершина develop, если коммит прогона уже не она и деплой
  пропущен (`check-tip.sh`);
- `state-recovery` — итог восстановления состояния OpenTofu после обрыва
  (`recover-state.sh`), строки `ключ=значение`; `s3-watchdog.log` — когда
  сторож закрыл туннель к зависшему хранилищу (`.github/scripts/s3-watchdog.sh`).

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

# Все тома стека — с данными (база, реплика, бэкапы, очереди): их уничтожение —
# потеря данных, замена тома — тоже уничтожение (delete, create). Сторож
# отклоняет delete у любого docker_volume, а не по списку адресов или имён:
# ресурс, переименованный без `moved` (и с новым именем тома), уничтожается по
# старому адресу, которого в списке уже нет, и prevent_destroy там тоже нет.
# Сменить том — `removed` с `destroy = false`: план выйдет `forget`, а не
# `delete` (infra/README.md).
VOLUME = "docker_volume"

# Контейнеры базы и брокера: деплой их не пересоздаёт. Замена гасит базу
# посреди транзакций старого API и воркера, а повод к ней — смена env, памяти
# или log_opts — на томе часто и не срабатывает: POSTGRES_PASSWORD postgres
# читает только при инициализации тома, и после замены миграции падают на
# пароле (#213). Такая правка — ручным запуском деплоя с
# `allow_stateful_replace` (`ALLOW_STATEFUL_REPLACE=true` в env, его ставит
# только workflow_dispatch): тогда замена — предупреждение, а не отказ. Тома
# (любой docker_volume) флаг не открывает никогда.
#
# Сверка — и по адресу, и по имени контейнера на хосте (`change.before.name`,
# `change.after.name`): ресурс, переименованный без `moved` (и с правкой
# этого списка), уничтожается по старому адресу, которого здесь уже нет, —
# тот же обход, что #219 закрыл для томов (ревью #220). Имя на хосте
# уникально. Адреса нужны и сами: их поднимает `-target` в infra.sh, а в
# урезанных фикстурах плана нет before/after.
STATEFUL = ("docker_container.postgres", "docker_container.rabbitmq")
STATEFUL_NAMES = ("dmc268-postgres", "dmc268-rabbitmq")

# Планы деплоя: имя у команды `plan` → файл в каталоге отчёта и подпись в
# summary. Порядок — порядок шагов.
PLANS = {
    "infra": ("plan-infra.json", "База и брокер — targeted-план шага «Поднять базу и брокер»"),
    "full": ("plan.json", "Полный выкат"),
}

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
    ("start", "Подготовка: checkout", ("start", "checkout")),
    ("tip", "Сверить коммит с вершиной develop; SSH, OpenTofu, хранилище состояния, вход в ghcr",
     ("tip", "ssh", "tofu", "s3", "login")),
    ("diagnostics", "Доставки вебхука и сбои воркера с прошлого выката", ("diagnostics",)),
    ("infra", "Поднять базу и брокер", ("infra",)),
    ("plan", "Спланировать и проверить план", ("plan",)),
    ("migrate", "Применить миграции", ("migrate",)),
    ("apply", "Применить конфигурацию", ("apply",)),
    ("recover", "Восстановить состояние OpenTofu", ("recover", "state_artifact")),
    ("api_logs", "Логи API после упавшего apply", ("api_logs",)),
    ("check_api", "Проверить, что API жив (через туннель)", ("check_api",)),
    ("check_worker", "Проверить, что воркер работает", ("check_worker",)),
    ("check_replica", "Проверить, что реплика получает поток", ("check_replica",)),
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
# Журнал — запасной путь для готовности API, основной — события Docker.
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
    """Тома, которые план уничтожает или заменяет, — по какому бы адресу они ни шли."""
    return [
        rc["address"]
        for rc in plan.get("resource_changes", [])
        if rc["type"] == VOLUME and "delete" in rc["change"]["actions"]
    ]


def stateful_deletes(plan: dict[str, Any]) -> list[str]:
    """Контейнеры базы и брокера, которые план пересоздаёт или удаляет, — по адресу или имени на хосте."""
    def stateful(rc: dict[str, Any]) -> bool:
        names = {(rc["change"].get(side) or {}).get("name") for side in ("before", "after")}
        return rc["address"] in STATEFUL or (rc["type"] == "docker_container" and bool(names & set(STATEFUL_NAMES)))

    return [
        rc["address"]
        for rc in plan.get("resource_changes", [])
        if "delete" in rc["change"]["actions"] and stateful(rc)
    ]


def plan_command(report_dir: Path, name: str, allowed_by: str | None) -> int:
    """Сторож плана; `allowed_by` — кто разрешил замену базы и брокера, None — никто."""
    plan = json.load(sys.stdin)
    changes = plan_changes(plan)
    deleted, recreated = protected_deletes(plan), stateful_deletes(plan)
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / PLANS[name][0]).write_text(json.dumps(
        {"changes": changes, "protected_deletes": deleted, "stateful_deletes": recreated,
         "stateful_allowed_by": allowed_by},
        ensure_ascii=False,
    ))
    print("\n".join(plan_table(changes)) if changes else "План без изменений.")
    for address in deleted:
        print(
            f"::error::план уничтожает том данных {address}: apply не запускается. "
            "Сменить том — новым ресурсом, а прежний снять с учёта блоком "
            "removed { lifecycle { destroy = false } }; данные останутся на сервере."
        )
    if recreated and allowed_by is not None:
        print(f"::warning::замена разрешена вручную ({allowed_by}): {', '.join(recreated)} — "
              "контейнер остановится (destroy_grace_seconds) и создастся заново на том же томе.")
        recreated = []
    for address in recreated:
        print(
            f"::error::план пересоздаёт или удаляет {address}: apply не запускается. "
            "Деплой не гасит базу и брокер: правка их env, памяти, log_opts или образа — "
            "ручным запуском `gh workflow run deploy.yml -f allow_stateful_replace=true`, "
            "ротация пароля — по runbook (#161). "
            "POSTGRES_PASSWORD на существующем томе замена контейнера не меняет."
        )
    if deleted or recreated:
        return 1
    print("Тома данных план не уничтожает, базу и брокер не пересоздаёт без разрешения.")
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
    новые соединения старый API уже не принимает. `Created` и `StartedAt`
    нового — из `docker inspect`. Готовность — событие `health_status: healthy`
    нового контейнера, неуспешные проверки до него — `exec_die` с `exitCode` ≠ 0
    (проверку здоровья Docker запускает как exec, а других exec в API во время
    apply никто не делает). Все отметки — с часов сервера.

    Нет событий нового контейнера (поток не подключился или оборвался) —
    запасной путь, `State.Health.Log`. Журнал держит пять последних проверок,
    после healthy — раз в 30 с: если apply шёл дольше пары минут после healthy,
    первая успешная из него вытеснена, и готовность выходит позже настоящей.
    Полный журнал поэтому недостоверен (`healthy_reliable`).
    """
    if new is None:
        return {"status": "missing"}
    if not old_id:
        return {"status": "created"}
    if new["Id"] == old_id:
        return {"status": "kept"}

    def of(container: str) -> list[dict[str, Any]]:
        return [e for e in events if e.get("Actor", {}).get("ID") == container]

    def at(event: dict[str, Any]) -> float:
        return int(event["timeNano"]) / 1e9

    def attrs(event: dict[str, Any]) -> dict[str, Any]:
        attributes: dict[str, Any] = event.get("Actor", {}).get("Attributes", {})
        return attributes

    def first(own: list[dict[str, Any]], action: str, **attributes: str) -> dict[str, Any] | None:
        return next(
            (e for e in own if e.get("Action") == action and all(attrs(e).get(k) == v for k, v in attributes.items())),
            None,
        )

    old_events, new_events = of(old_id), of(new["Id"])
    term, died = first(old_events, "kill", signal="15"), first(old_events, "die")
    created = parse_time(new["Created"])
    started_at = new["State"].get("StartedAt", "")
    started = parse_time(started_at) if not started_at.startswith("0001-") else None

    if new_events:
        healthy_event = first(new_events, "health_status: healthy")
        before = new_events[: new_events.index(healthy_event)] if healthy_event else new_events
        exits = [e for e in before if e.get("Action") == "exec_die"]
        healthy = at(healthy_event) if healthy_event else None
        failed = sum(1 for e in exits if attrs(e).get("exitCode") != "0")
        check = None
        success = next((e for e in reversed(exits) if attrs(e).get("exitCode") == "0"), None)
        if healthy_event and success:
            exec_id = attrs(success).get("execID")
            begun = next(
                (e for e in before if str(e.get("Action", "")).startswith("exec_create")
                 and attrs(e).get("execID") == exec_id),
                None,
            )
            check = round(at(success) - at(begun), 3) if begun else None
        source, reliable, at_least = "events", True, False
    else:
        log = (new["State"].get("Health") or {}).get("Log") or []
        index = next((i for i, entry in enumerate(log) if entry["ExitCode"] == 0), None)
        healthy = parse_time(log[index]["End"]) if index is not None else None
        failed = index if index is not None else len(log)
        check = round(healthy - parse_time(log[index]["Start"]), 3) if index is not None and healthy else None
        full = len(log) >= HEALTH_LOG_SIZE
        # Полный журнал: и успешная проверка могла быть не первой, и неуспешные
        # до неё могли вытесниться.
        source, reliable, at_least = "health_log", not full, full

    def span(a: float | None, b: float | None) -> float | None:
        return round(b - a, 3) if a is not None and b is not None else None

    term_at = at(term) if term else None
    died_at = at(died) if died else None
    return {
        "status": "replaced",
        "stop": span(term_at, died_at),
        "recreate": span(died_at, created),
        "start": span(created, started),
        "ready": span(started, healthy),
        "total": span(term_at, healthy),
        "failed_checks": failed,
        "failed_checks_at_least": at_least,
        "check": check,
        "healthy_from": source,
        "healthy_reliable": reliable,
    }


# --- миграции -------------------------------------------------------------

UPGRADE = re.compile(r"Running upgrade (\S*) -> ([^\s,]+)")


def current_revision(text: str) -> str:
    """Ревизия из вывода `alembic current`: `0012 (head)` → `0012`; пусто — пустая база.

    Логи миграций уходят в stderr (`alembic/env.py`, #232), и stdout несёт
    только ревизию. Строки-объекты JSON всё равно пропускаются — страховка
    на случай, если логи снова окажутся в stdout: раньше там шли `Context
    impl …` и `Will assume transactional DDL.`, ревизией становился
    `{"timestamp":`, и повтор по блокировке не срабатывал (ревью #130).
    """
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("{"):
            return line.split()[0]
    return ""


def migrations(log: str, outcome: str = "success", current: str | None = None) -> dict[str, Any]:
    """Применённые ревизии из вывода `alembic upgrade head`, исхода шага и ревизии базы.

    Строка `Running upgrade A -> B` пишется до ревизии B, а не после неё:
    при упавшем шаге последняя начатая и есть упавшая. Сколько из начатых
    зафиксировано, лог не говорит. Обычно ноль — upgrade идёт одной
    транзакцией (`alembic/env.py`), — но `autocommit_block` фиксирует всё до
    себя, а потерянный код выхода (оборванный ssh) скрывает и полный успех.
    Поэтому при сбое число берётся по `current` — ревизии базы из `alembic
    current` после сбоя (ревью #220); None — её узнать не удалось, и число
    тоже неизвестно (`applied` — None).
    """
    steps = UPGRADE.findall(log)
    if outcome != "failure":
        if not steps:
            return {"applied": 0}
        return {"applied": len(steps), "from": steps[0][0] or "пустая база", "to": steps[-1][1]}
    if not steps:
        return {"applied": 0, "current": current}
    chain = [steps[0][0], *(to for _, to in steps)]
    applied = chain.index(current) if current in chain else None
    return {"applied": applied, "from": steps[0][0] or "пустая база", "failed": steps[-1][1], "current": current}


# Миграция не дождалась блокировки (lock_timeout в alembic/env.py):
# `LockNotAvailable` у psycopg, SQLSTATE 55P03. statement_timeout (57014,
# `QueryCanceled`) сюда не входит: долгий запрос повтор не ускорит.
LOCK_NOT_AVAILABLE = re.compile(r"\bLockNotAvailable\b|\b55P03\b")


def lock_retry(log: str, current: str | None) -> bool:
    """Повторить ли упавший upgrade: не дождался блокировки, а база там, откуда попытка начала.

    Тогда транзакция откатилась целиком, и повтор начнёт с той же ревизии
    (ревью #220). Ревизия базы неизвестна (`current` — None), начатых ревизий
    в логе нет или база сдвинулась (`autocommit_block`) — не повторять: что
    делать, решает человек. DDL из `autocommit_block` самой упавшей ревизии
    мог остаться и при совпадении — повтор выполнит его снова; такой DDL пишут
    повторяемым (`IF NOT EXISTS`, скилл alembic-migration).
    """
    steps = UPGRADE.findall(log)
    return bool(LOCK_NOT_AVAILABLE.search(log)) and bool(steps) and current is not None and current == steps[0][0]


def migrate_retry_command(report_dir: Path) -> int:
    """0 — повторить upgrade (migrate.sh), 1 — нет."""
    if (report_dir / "migrate-current.error").exists() or not (report_dir / "migrate-current.log").exists():
        current = None
    else:
        current = current_revision(read(report_dir / "migrate-current.log"))
    return 0 if lock_retry(read(report_dir / "migrate.log"), current) else 1


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

    stale_tip = read(report_dir / "stale-tip").strip()
    skipped = bool(stale_tip) and result == "success"
    headline = {"success": "успешно", "failure": "упал", "cancellation": "отменён"}.get(result, result)
    if skipped:
        headline = f"пропущен: вершина develop уже `{stale_tip}`"
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

    if skipped:
        out += ["", (f"Коммит прогона уже не вершина develop (`{stale_tip}`): стенд не трогали, "
                "новый коммит выкатывает свой прогон."), "", *steps_section(steps, marks)]
        return "\n".join(out) + "\n"

    out += ["", *downtime_section(report_dir), "", *plan_section(report_dir)]
    if recovery := state_section(report_dir, env.get("ALLOW_STATEFUL_REPLACE") == "true"):
        out += ["", *recovery]
    migrate = steps.get("migrate", {}).get("outcome", "")
    out += ["", *migrations_section(report_dir, marks, migrate), "", *disk_section(report_dir)]
    out += ["", *steps_section(steps, marks)]
    return "\n".join(out) + "\n"


def downtime_section(report_dir: Path) -> list[str]:
    out = ["### Простой API", ""]
    if not (report_dir / "api-old-id").exists():
        return [*out, "Apply не запускался."]
    for name, what in (("api-old-id", "прежнем"), ("api-new.json", "новом")):
        if (report_dir / f"{name}.error").exists():
            code = read(report_dir / f"{name}.error").strip()
            return [*out, f"Не удалось спросить сервер о {what} контейнере API (ssh или timeout, код {code}): простой не посчитать."]
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
    ready_mark = "`health_status: healthy`" if d["healthy_from"] == "events" else "`Health.Log[].End`"
    out += [
        "| Этап | Ключ | с |",
        "| --- | --- | ---: |",
        f"| Остановка старого: SIGTERM → выход процесса | `dmc268.deploy.api.downtime.stop` | {seconds(d['stop'])} |",
        f"| Удаление старого и создание нового → `Created` | `dmc268.deploy.api.downtime.recreate` | {seconds(d['recreate'])} |",
        f"| Старт процесса: `Created` → `State.StartedAt` | `dmc268.deploy.api.downtime.start` | {seconds(d['start'])} |",
        f"| До первой успешной проверки: `StartedAt` → {ready_mark} | `dmc268.deploy.api.downtime.ready` | {seconds(d['ready'])} |",
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
    if d["healthy_from"] == "health_log":
        note += " Событий нового контейнера в потоке `docker events` нет: готовность — из `State.Health.Log`."
    out.append(note)
    if not d["healthy_reliable"]:
        out += ["", "> [!WARNING]", ("> Готовность и простой недостоверны: журнал проверок полон, первая успешная "
                "проверка могла из него вытесниться, и простой тогда завышен.")]
    return out


def read_pairs(text: str) -> dict[str, str]:
    """Строки `ключ=значение`; значение — до конца строки."""
    pairs = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            pairs[key.strip()] = value.strip()
    return pairs


def state_section(report_dir: Path, overridden: bool = False) -> list[str]:
    """Восстановление состояния после обрыва; нет файла — шаг не запускался, раздела нет.

    `overridden` — у этой попытки был `allow_stateful_replace`: Re-run его не
    получит (флаг — только у первой попытки), и доделать выкат замены базы
    может лишь новый ручной запуск (ревью #220).
    """
    if not (report_dir / "state-recovery").exists():
        return []
    r = read_pairs(read(report_dir / "state-recovery"))
    out = ["### Состояние OpenTofu", ""]
    watchdog = read(report_dir / "s3-watchdog.log").strip().splitlines()
    if watchdog:
        out += [f"Сторож закрыл туннель к хранилищу: {watchdog[-1]}.", ""]
    lock = f"блокировка `{r['lock']}`" if r.get("lock") else "блокировка"
    result = r.get("result", "")
    rerun = ("Re-run флага `allow_stateful_replace` не получает: доделает выкат новый ручной запуск "
             "`gh workflow run deploy.yml --ref develop -f allow_stateful_replace=true`." if overridden
             else "Перезапуск деплоя доделает выкат без ручных действий.")
    if result == "restored":
        return [*out, (f"Состояние восстановлено: несохранённое состояние (`errored.tfstate`, serial {r.get('serial', '?')}) "
                f"записано в хранилище, {lock} снята. {rerun}")]
    if result == "unlocked":
        return [*out, f"Состояние было записано, не снялась только {lock}: снята. {rerun}"]
    if result == "foreign":
        return [*out, (f"{lock[0].upper() + lock[1:]} — не этого запуска (`{r.get('who', '?')}`): восстановление её не трогает, "
                "несохранённого состояния этот запуск не оставил.")]
    if result == "none":
        # Шаг шёл, потому что упал infra, plan или apply, — часто не из-за связи
        # (отказ сторожа в infra.sh, ошибка плана): раздел о том, что
        # восстанавливать нечего, только уводит от причины. Остаётся пометка
        # сторожа хранилища, если он сработал.
        return out[:-1] if watchdog else []
    if result == "released":
        # Блокировку оборванного запуска (другого раннера или этого, убитого
        # отменой) сняли, не зная, записалось ли состояние под ней.
        who = f"`{r['who']}`, " if r.get("who") else ""
        out.append(f"Снята {lock} оборвавшегося запуска ({who}{r.get('operation', '?')}, {r.get('created', '?')}).")
        if r.get("operation") == "OperationTypePlan":
            return [*out[:-1], out[-1] + " План состояние не пишет: оно не пострадало."]
        return [*out, "", "> [!WARNING]",
                ("> Состояние в хранилище может быть старше стенда: записалось ли оно под этой блокировкой, "
                 "неизвестно. Если план перезапуска создаёт контейнеры, которые на сервере уже есть, — "
                 "`infra/README.md`, «Если деплой упал посреди apply», шаг 4.")]
    out += ["> [!CAUTION]", f"> Состояние не восстановлено: {r.get('reason', 'шаг оборвался')}.",
            ("> Пока его не восстановить, следующий деплой упрётся в блокировку или в старое состояние. "
             "Что делать — `infra/README.md`, «Если деплой упал посреди apply».")]
    if (report_dir / "errored.tfstate.enc").exists():
        out.append(f"> Несохранённое состояние (serial {r.get('serial', '?')}) — в артефакте `errored-tfstate` этого "
                   "прогона, зашифрованное ключом `STATE_BACKUP_KEY_DMC268_T1`; артефакт живёт сутки.")
    return out


def plan_section(report_dir: Path) -> list[str]:
    out = ["### План OpenTofu"]
    for file, title in PLANS.values():
        out += ["", f"#### {title}", ""]
        plan = read_json(report_dir / file)
        if plan is None:
            out.append("Плана нет: шаг планирования не дошёл до него.")
            continue
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
            out += ["", "Тома данных план не уничтожает."]
        stateful = ", ".join(f"`{a}`" for a in plan["stateful_deletes"])
        if plan["stateful_deletes"] and plan.get("stateful_allowed_by") is not None:
            out += ["", "> [!WARNING]", (f"> Замена {stateful} разрешена вручную: запуск с `allow_stateful_replace` "
                    f"({plan['stateful_allowed_by']}). Ключ — `dmc268.deploy.plan.stateful_allowed_by`.")]
        elif plan["stateful_deletes"]:
            out += ["", "> [!CAUTION]", (f"> Сторож остановил деплой до apply: план пересоздаёт или удаляет {stateful}"
                    " (`dmc268.deploy.plan.stateful_deletes`). Разрешить — ручным запуском с "
                    "`allow_stateful_replace`; ротация пароля — по runbook (#161).")]
        else:
            out += ["", "Базу и брокер (`" + "`, `".join(STATEFUL) + "`) план не пересоздаёт."]
    return out


def migrations_section(report_dir: Path, marks: dict[str, float], outcome: str) -> list[str]:
    out = ["### Миграции", ""]
    if not (report_dir / "migrate.log").exists():
        if outcome == "failure":
            # migrate.log появляется с запуском alembic; до него — tofu output и pull.
            return [*out, "Шаг миграций упал до alembic (`tofu output` или pull образа): схема не менялась."]
        return [*out, "Шаг миграций не запускался."]
    if outcome == "cancelled":
        # Отмена убивает docker CLI на раннере, а не контейнер на сервере:
        # миграции могли и дойти до конца, и откатиться (trap в migrate.sh).
        return [*out, ("Шаг миграций прерван: применились ли ревизии, по логу не сказать. "
                "Ревизию базы покажет `alembic current` или следующий деплой.")]
    # Упал шаг пароля роли мониторинга после удачного upgrade (migrate.sh):
    # ревизии зафиксированы, и про миграции summary пишет как про успех.
    monitor_error = read(report_dir / "migrate-monitor.error").strip()
    if outcome == "failure" and monitor_error:
        return [*migrations_section(report_dir, marks, "success"), "", "> [!CAUTION]",
                (f"> Миграции применены, но пароль роли мониторинга `dmc268_monitor` не обновлён "
                 f"(код {monitor_error}): коллектор не войдёт в базу с паролем из секрета. Повторите деплой.")]
    # Ответ `alembic current` после сбоя (migrate.sh); нет файла или рядом
    # `.error` — база не ответила, ревизия неизвестна.
    answered = (report_dir / "migrate-current.log").exists() and not (report_dir / "migrate-current.error").exists()
    current = current_revision(read(report_dir / "migrate-current.log")) if outcome == "failure" and answered else None
    m = migrations(read(report_dir / "migrate.log"), outcome, current)
    applied = "неизвестно" if m["applied"] is None else m["applied"]
    out += ["| | Ключ | Значение |", "| --- | --- | --- |",
            f"| Применено ревизий | `dmc268.deploy.migrations.applied` | {applied} |"]
    if "failed" in m:
        out.append(f"| Упала на ревизии | `dmc268.deploy.migrations.revision.failed` | `{m['failed']}` |")
    elif m["applied"]:
        out += [f"| Ревизия до | `dmc268.deploy.migrations.revision.from` | `{m['from']}` |",
                f"| Ревизия после | `dmc268.deploy.migrations.revision.to` | `{m['to']}` |"]
    if outcome == "failure":
        base = "неизвестна" if m["current"] is None else f"`{m['current'] or 'пустая база'}`"
        out.append(f"| Ревизия базы | `dmc268.deploy.migrations.revision.current` | {base} |")
    attempts = read(report_dir / "migrate-attempts").strip()
    if attempts.isdigit() and int(attempts) > 1:
        # Повторы после LockNotAvailable (migrate.sh): прежние попытки откатились целиком.
        out.append(f"| Попыток | `dmc268.deploy.migrations.attempts` | {attempts} |")
    out.append(f"| Время шага, с | | {seconds(durations(marks).get('migrate'))} |")
    if outcome != "failure":
        return out
    out += ["", "> [!CAUTION]"]
    if "failed" not in m:
        out.append("> Шаг миграций упал до первой ревизии: схема не менялась. Причина — в логе шага.")
    elif m["current"] is None:
        out.append(f"> Миграция упала на `{m['failed']}`, ревизия базы неизвестна — проверьте `alembic current`.")
    elif m["applied"] == 0:
        out.append(f"> Шаг миграций упал на ревизии `{m['failed']}`: транзакция миграций откатена, применено 0 — "
                   f"база на `{m['current'] or 'пустая база'}` (`alembic current` после сбоя). Если `{m['failed']}` "
                   "успела выполнить `autocommit_block` (например, `ADD VALUE`), этот DDL зафиксирован, "
                   "хотя ревизия базы прежняя.")
    elif m["applied"] is None:
        out.append(f"> Шаг миграций упал на `{m['failed']}`, а база на `{m['current']}` — ревизии вне этого "
                   "запуска; проверьте `alembic history`.")
    else:
        out.append(f"> Шаг миграций упал на `{m['failed']}`, но база на `{m['current']}`: зафиксировано "
                   f"{m['applied']} — `autocommit_block` или commit с потерянным кодом выхода.")
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
    if argv in (["plan"], ["plan", "infra"]):
        # Только буквальное `true`: его даёт выражение в env шага из ручного запуска.
        allowed = os.environ.get("ALLOW_STATEFUL_REPLACE") == "true"
        allowed_by = (os.environ.get("DEPLOY_ACTOR") or "кто — неизвестно") if allowed else None
        return plan_command(report_dir, argv[1] if len(argv) == 2 else "full", allowed_by)
    if argv == ["migrate-retry"]:
        return migrate_retry_command(report_dir)
    if argv == ["render"]:
        sys.stdout.write(render(report_dir, dict(os.environ)))
        return 0
    print("использование: deploy_report.py plan [infra]|migrate-retry|render", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
