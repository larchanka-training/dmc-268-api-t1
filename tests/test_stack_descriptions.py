"""Два описания одного стека — сверены по тем фактам, что обязаны совпадать.

`docker-compose.yml` описывает локальный стек, `infra/main.tf` — серверный.
Оба обязательны и законно различаются: compose собирает образ и поднимает
Redis, infra забирает готовый тег и поднимает RabbitMQ. Сверять их целиком
бессмысленно.

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

NOT_FOUND = "не найдено"

COMPOSE = Path("docker-compose.yml")
INFRA = Path("infra/main.tf")


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
    """Compose применяет миграции командой сервиса api."""
    return "alembic upgrade head" in text


def infra_applies_migrations(text: str) -> bool:
    """В Terraform миграции — отдельный ресурс, от которого зависит api.

    Одного наличия ресурса мало: без зависимости API стартовал бы параллельно
    с миграциями, то есть ровно так же, как до появления этого ресурса.
    """
    has_resource = bool(
        re.search(r"resource\s+\"docker_container\"\s+\"migrate\"", text)
    )
    api_depends = "docker_container.migrate" in text
    return has_resource and api_depends


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
    assert compose_applies_migrations("command: alembic upgrade head && uvicorn")
    assert infra_applies_migrations(
        'resource "docker_container" "migrate" {}\n'
        "depends_on = [docker_container.migrate]"
    )
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


def test_extractors_report_absence_instead_of_a_blank() -> None:
    """Два «не найдено» не должны выглядеть как совпадение."""
    assert compose_postgres_image("ничего похожего") == NOT_FOUND
    assert infra_postgres_image("ничего похожего") == NOT_FOUND
    assert compose_postgres_volume_path("ничего похожего") == NOT_FOUND
    assert infra_postgres_volume_path("ничего похожего") == NOT_FOUND
    assert not compose_applies_migrations("ничего похожего")
    assert not infra_applies_migrations('resource "docker_container" "migrate" {}')
    assert compose_worker_model_env("ничего похожего") == frozenset()
    assert infra_worker_model_env("ничего похожего") == frozenset()


def test_both_descriptions_use_the_same_postgres_image() -> None:
    compose = compose_postgres_image(COMPOSE.read_text())
    infra = infra_postgres_image(INFRA.read_text())
    assert compose != NOT_FOUND, "образ PostgreSQL не найден в docker-compose.yml"
    assert infra != NOT_FOUND, "образ PostgreSQL не найден в infra/main.tf"
    assert compose == infra, (
        f"образ PostgreSQL разошёлся: compose {compose}, infra {infra}"
    )


def test_both_descriptions_mount_the_postgres_volume_the_same_way() -> None:
    """Расхождение здесь однажды уложило postgres:18 в рестарт-луп."""
    compose = compose_postgres_volume_path(COMPOSE.read_text())
    infra = infra_postgres_volume_path(INFRA.read_text())
    assert compose != NOT_FOUND, "точка монтирования не найдена в docker-compose.yml"
    assert infra != NOT_FOUND, "точка монтирования не найдена в infra/main.tf"
    assert compose == infra, (
        f"точка монтирования тома PostgreSQL разошлась: "
        f"compose {compose}, infra {infra}"
    )


def test_both_descriptions_apply_migrations_before_serving() -> None:
    """Расхождение здесь однажды оставило API против непромигрированной базы."""
    assert compose_applies_migrations(
        COMPOSE.read_text()
    ), "docker-compose.yml не применяет миграции"
    assert infra_applies_migrations(
        INFRA.read_text()
    ), "infra/main.tf не применяет миграции до старта API"


def test_both_descriptions_give_the_worker_the_same_model_settings() -> None:
    """Без `LLM_BACKEND` воркер не стартует: описание без неё роняет деплой."""
    compose = compose_worker_model_env(COMPOSE.read_text())
    infra = infra_worker_model_env(INFRA.read_text())
    assert "LLM_BACKEND" in compose, "docker-compose.yml не передаёт воркеру LLM_BACKEND"
    assert "LLM_BACKEND" in infra, "infra/main.tf не передаёт воркеру LLM_BACKEND"
    assert compose == infra, (
        f"переменные модели воркера разошлись: только в compose {sorted(compose - infra)}, "
        f"только в infra {sorted(infra - compose)}"
    )
