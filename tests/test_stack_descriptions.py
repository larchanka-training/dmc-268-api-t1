"""Два описания одного стека — сверены по тем фактам, что обязаны совпадать.

`docker-compose.yml` описывает локальный стек, `infra/main.tf` — серверный.
Оба обязательны и законно различаются: compose собирает образ и поднимает
Redis, infra забирает готовый тег и поднимает RabbitMQ. Сверять их целиком
бессмысленно.

Совпадать обязаны инварианты из спеки `backend-delivery`: миграции применяются
до приёма трафика, том PostgreSQL монтируется по конвенции используемого
мажорного релиза, и релиз этот один и тот же. Оба раза, когда эти факты
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


def postgres_major(text: str) -> str:
    """Мажорный релиз образа PostgreSQL. Формат тега одинаков в обоих файлах."""
    match = re.search(r"postgres:(\d+)[-\w.]*", text)
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


def test_extractors_find_facts_that_are_present() -> None:
    """Шаблон, переставший находить факт, обязан падать здесь, а не молчать."""
    assert postgres_major('name = "postgres:18-alpine"') == "18"
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


def test_extractors_report_absence_instead_of_a_blank() -> None:
    """Два «не найдено» не должны выглядеть как совпадение."""
    assert postgres_major("ничего похожего") == NOT_FOUND
    assert compose_postgres_volume_path("ничего похожего") == NOT_FOUND
    assert infra_postgres_volume_path("ничего похожего") == NOT_FOUND
    assert not compose_applies_migrations("ничего похожего")
    assert not infra_applies_migrations('resource "docker_container" "migrate" {}')


def test_both_descriptions_use_the_same_postgres_major() -> None:
    compose = postgres_major(COMPOSE.read_text())
    infra = postgres_major(INFRA.read_text())
    assert compose != NOT_FOUND, "версия PostgreSQL не найдена в docker-compose.yml"
    assert infra != NOT_FOUND, "версия PostgreSQL не найдена в infra/main.tf"
    assert compose == infra, (
        f"мажорный релиз PostgreSQL разошёлся: compose {compose}, infra {infra}"
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
