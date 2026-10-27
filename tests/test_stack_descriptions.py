"""Два описания одного стека — сверены по тем фактам, что обязаны совпадать.

`docker-compose.yml` описывает локальный стек, `infra/*.tf` вместе с шагами
`.github/workflows/deploy.yml` — серверный. Оба обязательны и законно
различаются: compose собирает образ и поднимает Redis, infra забирает готовый
тег и поднимает RabbitMQ, а миграции на сервере — шаг деплоя, а не контейнер
стека. Сверять их целиком бессмысленно.

Совпадать обязаны инварианты из спеки `backend-delivery`: миграции применяются
до приёма трафика, том PostgreSQL монтируется по конвенции используемого
мажорного релиза, и образ один и тот же — вплоть до сборки. Оба раза, когда эти факты
разъезжались, мы узнавали об этом деплоем — отсюда и файл, по той же причине,
что и `test_docs.py`.

Извлечение фактов проверяется отдельно: шаблон, переставший находить факт,
обязан ронять проверку разбора, а не молча делать сверку успешной. Ровно на
этом — кавычки в объявлении ресурса Terraform — уже была получена ложная
картина при ручной сверке.
"""

import re
from pathlib import Path

from tests.infra_text import read_infra_text
from tests.shell_text import commands

NOT_FOUND = "не найдено"

COMPOSE = Path("docker-compose.yml")
DOCKERFILE = Path("Dockerfile")
DEPLOY_WORKFLOW = Path(".github/workflows/deploy.yml")
MIGRATE_SCRIPT = Path(".github/scripts/deploy/migrate.sh")
APPLY_SCRIPT = Path(".github/scripts/deploy/apply.sh")


# Образ сверяется целиком, а не по мажорному релизу: `postgres:18-alpine` и
# `pgvector/pgvector:pg18` — одна версия, но разные библиотеки C (musl и glibc).
# Сортировка строк у них разная, и текстовые индексы тома, созданного одной
# сборкой, под другой неконсистентны. Ищется само объявление, а не первое
# упоминание: комментарии рядом называют и прежний образ.


def compose_postgres_image(text: str) -> str:
    """Образ сервиса postgres в compose."""
    match = re.search(
        r"^\s*postgres:\s*\n(?:\s*#.*\n)*\s*image:\s*(\S+)", text, re.MULTILINE
    )
    return match.group(1) if match else NOT_FOUND


def infra_postgres_image(text: str) -> str:
    """Образ из ресурса docker_image.postgres в конфигурации Terraform."""
    match = re.search(
        r"resource\s+\"docker_image\"\s+\"postgres\"\s*\{[^}]*?\bname\s*=\s*\"([^\"]+)\"",
        text,
    )
    return match.group(1) if match else NOT_FOUND


def compose_postgres_volume_path(text: str) -> str:
    """Точка монтирования тома PostgreSQL в compose."""
    match = re.search(r"postgres_data:(/var/lib/postgresql[^\s\"']*)", text)
    return match.group(1) if match else NOT_FOUND


def infra_postgres_volume_path(text: str) -> str:
    """Точка монтирования тома PostgreSQL в конфигурации Terraform."""
    match = re.search(
        r"volume_name\s*=\s*docker_volume\.postgres\.name"
        r".*?container_path\s*=\s*\"([^\"]+)\"",
        text,
        re.DOTALL,
    )
    return match.group(1) if match else NOT_FOUND


def compose_applies_migrations(text: str) -> bool:
    """Compose применяет миграции отдельным сервисом, от которого зависят api и воркер.

    Одного сервиса мало: без `service_completed_successfully` API стартовал бы
    параллельно с миграциями (или против непромигрированной базы).
    """
    migrate = re.search(r"^  migrate:\n(.*?)(?=^  \S)", text, re.MULTILINE | re.DOTALL)
    if not migrate or not re.search(r'"alembic",\s*"upgrade",\s*"head"', migrate.group(1)):
        return False
    waits = re.findall(
        r"migrate:\s*\n\s*condition:\s*service_completed_successfully", text
    )
    return len(waits) >= 2


def deploy_migrates_before_apply(workflow: str, apply_script: str) -> bool:
    """На сервере миграции — шаг деплоя, и стоит он раньше полного `tofu apply`.

    Полный apply выкатывает API и воркер; шаг после него пустил бы новый код
    на непромигрированную базу. Полный apply — шаг со скриптом apply.sh, и в
    скрипте `tofu apply` без `-target`; `apply -target` для базы и брокера — не он.
    """
    def call(script: str) -> re.Match[str] | None:
        return re.search(
            rf"^[ \t]*(?:- )?(?:run: )?\.github/scripts/deploy/{re.escape(script)}[ \t]*$", workflow, re.MULTILINE
        )

    migrate, apply = call("migrate.sh"), call("apply.sh")
    # Команда целиком, с продолжениями строки: `-target` бывает на следующей.
    applies = re.finditer(r"^[ \t]*tofu\b[^\n]*\bapply\b(?:[^\n]*\\\n)*[^\n]*$", apply_script, re.MULTILINE)
    full_apply = any("-target" not in a.group(0) for a in applies)
    return bool(migrate and apply and full_apply and migrate.start() < apply.start())


def script_runs_migrations(script: str) -> bool:
    """Скрипт применяет `alembic upgrade head` одноразовым контейнером образа выката."""
    return bool(
        re.search(
            r"^\s*docker run --rm\b(?:[^\n]*\\\n)*?[^\n]*\"\$API_IMAGE\"\s+alembic upgrade head"
            r"(?:\s+2>&1\s*\|\s*tee\s[^\n]*)?\s*$",
            commands(script),
            re.MULTILINE,
        )
    )


# Ограничения процесса миграций (спека backend-delivery, «Контейнеры стека
# работают с минимальными привилегиями») — те же, что у api и worker в
# infra/app.tf, которые сверяет infra/tests/hardening.tftest.hcl.
MIGRATION_RESTRICTIONS = (
    "--read-only",
    "--tmpfs /tmp:rw,noexec,nosuid,size=64m",
    "--security-opt no-new-privileges:true",
    "--cap-drop ALL",
    "--init",
    "--memory 256m",
    "--memory-swap 256m",
)


def missing_migration_restrictions(script: str) -> list[str]:
    """Ограничения, которых нет среди флагов `docker run` скрипта миграций.

    Флаги из массива bash (`"${имя[@]}"`) подставляются в запуск (`commands`).
    """
    run = re.search(r"^\s*docker run --rm\b(?:[^\n]*\\\n)*[^\n]*$", commands(script), re.MULTILINE)
    flags = run.group(0) if run else ""
    return [flag for flag in MIGRATION_RESTRICTIONS if not re.search(rf"(?<!\S){re.escape(flag)}(?!\S)", flags)]


# Переменные модели воркера (change `add-llm-gateway`): без `LLM_BACKEND` воркер
# не стартует, и описание, которое её не передаёт, роняет деплой проверкой
# устойчивой работы воркера.
_MODEL_ENV = r"(?:LLM|OLLAMA|EUROROUTER)_[A-Z_]+"


def compose_worker_model_env(text: str) -> frozenset[str]:
    """Имена переменных модели в окружении сервиса worker в compose."""
    block = re.search(r"^  worker:\n(.*?)(?=^  \S|^\S|\Z)", text, re.MULTILINE | re.DOTALL)
    if block is None:
        return frozenset()
    return frozenset(re.findall(rf"^\s+({_MODEL_ENV}):", block.group(1), re.MULTILINE))


def infra_worker_model_env(text: str) -> frozenset[str]:
    """Имена переменных модели в окружении контейнера воркера в Terraform."""
    block = re.search(
        r"resource\s+\"docker_container\"\s+\"worker\"\s*\{(.*?)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    if block is None:
        return frozenset()
    return frozenset(re.findall(rf"\"({_MODEL_ENV})=", block.group(1)))


def uvicorn_graceful_timeout(text: str) -> int | None:
    """`--timeout-graceful-shutdown` команды uvicorn — в строке shell или в списке exec."""
    match = re.search(r"--timeout-graceful-shutdown\"?\s*,?\s*\"?(\d+)", text)
    return int(match.group(1)) if match else None


def infra_api_destroy_grace(text: str) -> int | None:
    """`destroy_grace_seconds` контейнера API в Terraform."""
    block = re.search(
        r"resource\s+\"docker_container\"\s+\"api\"\s*\{(.*?)^\}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    match = block and re.search(r"destroy_grace_seconds\s*=\s*(\d+)", block.group(1))
    return int(match.group(1)) if match else None


def test_extractors_find_facts_that_are_present() -> None:
    """Шаблон, переставший находить факт, обязан падать здесь, а не молчать."""
    assert (
        compose_postgres_image(
            "services:\n  postgres:\n    # комментарий\n    image: pgvector/pgvector:pg18\n"
        )
        == "pgvector/pgvector:pg18"
    )
    assert (
        infra_postgres_image(
            '# прежний образ: postgres:18-alpine\n'
            'resource "docker_image" "postgres" {\n'
            '  # комментарий\n'
            '  name         = "pgvector/pgvector:pg18"\n}'
        )
        == "pgvector/pgvector:pg18"
    )
    assert compose_postgres_volume_path("- postgres_data:/var/lib/postgresql") == (
        "/var/lib/postgresql"
    )
    assert (
        infra_postgres_volume_path(
            'volume_name = docker_volume.postgres.name\n'
            '    container_path = "/var/lib/postgresql"'
        )
        == "/var/lib/postgresql"
    )
    assert compose_applies_migrations(
        '  migrate:\n    command: ["alembic", "upgrade", "head"]\n  api:\n'
        "    depends_on:\n      migrate:\n        condition: service_completed_successfully\n"
        "  worker:\n    depends_on:\n      migrate:\n"
        "        condition: service_completed_successfully\n"
    )
    assert deploy_migrates_before_apply(
        "          tofu apply -auto-approve \\\n            -target=docker_container.postgres\n"
        "        run: |\n          .github/scripts/deploy/mark.sh\n          .github/scripts/deploy/migrate.sh\n"
        "      - run: .github/scripts/deploy/apply.sh\n",
        "tofu -chdir=infra apply -input=false -no-color tfplan\n",
    )
    assert script_runs_migrations(
        'docker run --rm --name x \\\n  --init \\\n  "$API_IMAGE" alembic upgrade head\n'
    )
    assert script_runs_migrations(
        'docker run --rm \\\n  "$API_IMAGE" alembic upgrade head 2>&1 | tee "$dir/migrate.log"\n'
    )
    assert missing_migration_restrictions(
        "docker run --rm \\\n  " + " \\\n  ".join(MIGRATION_RESTRICTIONS) + ' \\\n  "$API_IMAGE" x\n'
    ) == []
    # Общие флаги в массиве bash — тот же запуск.
    assert missing_migration_restrictions(
        "run=(\n  docker run --rm\n  " + "\n  ".join(MIGRATION_RESTRICTIONS) + '\n)\n"${run[@]}" "$API_IMAGE" x\n'
    ) == []
    assert script_runs_migrations('run=(\n  docker run --rm\n)\n"${run[@]}" "$API_IMAGE" alembic upgrade head\n')
    assert compose_worker_model_env(
        "services:\n  worker:\n    environment:\n      # комментарий\n"
        "      LLM_BACKEND: stub\n      DATABASE_URL: x\n  ollama:\n    environment:\n"
        "      OLLAMA_HOST: 0.0.0.0\n"
    ) == frozenset({"LLM_BACKEND"})
    assert infra_worker_model_env(
        'resource "docker_container" "worker" {\n'
        '  env = concat(local.app_env, [\n'
        '    "LLM_BACKEND=${var.llm_backend}",\n'
        '    "GITHUB_APP_ID=${var.github_app_id}",\n'
        "  ])\n}\n"
        'resource "docker_container" "api" {\n  env = ["OLLAMA_MODEL=x"]\n}\n'
    ) == frozenset({"LLM_BACKEND"})
    assert uvicorn_graceful_timeout('"--port", "8000", "--timeout-graceful-shutdown", "5"]') == 5
    assert uvicorn_graceful_timeout("exec uvicorn app.main:app --timeout-graceful-shutdown 7") == 7
    assert (
        infra_api_destroy_grace(
            'resource "docker_container" "worker" {\n  destroy_grace_seconds = 99\n}\n'
            'resource "docker_container" "api" {\n  destroy_grace_seconds = 10\n}\n'
        )
        == 10
    )


def test_extractors_report_absence_instead_of_a_blank() -> None:
    """Два «не найдено» не должны выглядеть как совпадение."""
    assert compose_postgres_image("ничего похожего") == NOT_FOUND
    assert infra_postgres_image("ничего похожего") == NOT_FOUND
    assert compose_postgres_volume_path("ничего похожего") == NOT_FOUND
    assert infra_postgres_volume_path("ничего похожего") == NOT_FOUND
    assert not compose_applies_migrations("ничего похожего")
    # Миграции в команде API, как было раньше, больше не считаются.
    assert not compose_applies_migrations("command: alembic upgrade head && uvicorn")
    # Шаг миграций после полного apply — то же, что без миграций.
    full = "tofu -chdir=infra apply -input=false tfplan\n"
    assert not deploy_migrates_before_apply(
        "      - run: .github/scripts/deploy/apply.sh\n      - run: .github/scripts/deploy/migrate.sh\n", full
    )
    # Без шага полного apply `apply -target` раньше миграций выкатом не
    # считается; скрипт, где apply только с -target (и на строке продолжения),
    # — тоже.
    assert not deploy_migrates_before_apply(
        "          tofu apply -auto-approve \\\n            -target=docker_container.postgres\n"
        "      - run: .github/scripts/deploy/migrate.sh\n", full
    )
    assert not deploy_migrates_before_apply(
        "      - run: .github/scripts/deploy/migrate.sh\n      - run: .github/scripts/deploy/apply.sh\n",
        "tofu -chdir=infra apply \\\n  -target=docker_container.api tfplan\n",
    )
    # Закомментированный вызов — не вызов.
    assert not script_runs_migrations('# docker run --rm "$API_IMAGE" alembic upgrade head\n')
    # Флаг из соседней команды не засчитывается, `--init` — не `--initx`.
    assert missing_migration_restrictions(
        "docker run --rm --initx \"$API_IMAGE\" x\necho --read-only\n"
    ) == list(MIGRATION_RESTRICTIONS)
    assert compose_worker_model_env("ничего похожего") == frozenset()
    assert infra_worker_model_env("ничего похожего") == frozenset()
    assert uvicorn_graceful_timeout("ничего похожего") is None
    assert infra_api_destroy_grace("ничего похожего") is None


def test_both_descriptions_use_the_same_postgres_image() -> None:
    compose = compose_postgres_image(COMPOSE.read_text())
    infra = infra_postgres_image(read_infra_text())
    assert compose != NOT_FOUND, "образ PostgreSQL не найден в docker-compose.yml"
    assert infra != NOT_FOUND, "образ PostgreSQL не найден в конфигурации infra/*.tf"
    assert compose == infra, (
        f"образ PostgreSQL разошёлся: compose {compose}, infra {infra}"
    )


def test_both_descriptions_mount_the_postgres_volume_the_same_way() -> None:
    """Расхождение здесь однажды уложило postgres:18 в рестарт-луп."""
    compose = compose_postgres_volume_path(COMPOSE.read_text())
    infra = infra_postgres_volume_path(read_infra_text())
    assert compose != NOT_FOUND, "точка монтирования не найдена в docker-compose.yml"
    assert infra != NOT_FOUND, "точка монтирования не найдена в конфигурации infra/*.tf"
    assert compose == infra, (
        f"точка монтирования тома PostgreSQL разошлась: "
        f"compose {compose}, infra {infra}"
    )


def test_both_descriptions_apply_migrations_before_serving() -> None:
    """Расхождение здесь однажды оставило API против непромигрированной базы."""
    assert compose_applies_migrations(
        COMPOSE.read_text()
    ), "docker-compose.yml не применяет миграции"
    assert deploy_migrates_before_apply(
        DEPLOY_WORKFLOW.read_text(), APPLY_SCRIPT.read_text()
    ), f"{DEPLOY_WORKFLOW}: нет шага {MIGRATE_SCRIPT} до полного tofu apply ({APPLY_SCRIPT})"
    assert script_runs_migrations(
        MIGRATE_SCRIPT.read_text()
    ), f"{MIGRATE_SCRIPT}: нет docker run --rm образа выката с alembic upgrade head"


def test_migrations_run_with_the_app_container_restrictions() -> None:
    """Снятая с миграций защита роняет CI, как у api и worker (hardening.tftest.hcl)."""
    missing = missing_migration_restrictions(MIGRATE_SCRIPT.read_text())
    assert not missing, f"{MIGRATE_SCRIPT}: у docker run нет {missing}"


def test_both_descriptions_give_the_worker_the_same_model_settings() -> None:
    """Без `LLM_BACKEND` воркер не стартует: описание без неё роняет деплой."""
    compose = compose_worker_model_env(COMPOSE.read_text())
    infra = infra_worker_model_env(read_infra_text())
    assert "LLM_BACKEND" in compose, "docker-compose.yml не передаёт воркеру LLM_BACKEND"
    assert "LLM_BACKEND" in infra, "infra/*.tf не передаёт воркеру LLM_BACKEND"
    assert compose == infra, (
        f"переменные модели воркера разошлись: только в compose {sorted(compose - infra)}, "
        f"только в infra {sorted(infra - compose)}"
    )


def test_the_api_stops_within_the_grace_period_despite_open_sse_streams() -> None:
    """Поток SSE живёт, пока открыта вкладка; без предела uvicorn ждал бы его до SIGKILL.

    Тогда lifespan не завершается и батч спанов теряется (спека `observability`).
    """
    grace = infra_api_destroy_grace(read_infra_text())
    assert grace is not None, "destroy_grace_seconds контейнера API не найден в конфигурации infra/*.tf"
    for path in (DOCKERFILE, COMPOSE):
        timeout = uvicorn_graceful_timeout(path.read_text())
        assert timeout is not None, f"{path}: uvicorn без --timeout-graceful-shutdown"
        assert timeout < grace, (
            f"{path}: --timeout-graceful-shutdown {timeout} не меньше грейс-периода API {grace} с"
        )
