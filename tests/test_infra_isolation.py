"""Изоляция серверного стека — по описанию `infra/main.tf`.

Спека `backend-delivery`: база, реплика и брокер живут во внутренней сети без
выхода наружу и не публикуют портов на хосте; интерфейс до них не дотягивается.
Ошибка здесь не ломает ни один запрос — стенд работает и с базой, открытой в
общую сеть, — поэтому ловить её некому, кроме этой проверки.

Как и в `test_stack_descriptions.py`, извлечение фактов проверяется отдельно:
шаблон, переставший находить ресурс, обязан ронять проверку разбора, а не
молча делать сверку успешной.
"""

import re
from pathlib import Path

INFRA = Path("infra/main.tf")

# Контейнеры с данными: только сеть данных, никаких портов на хосте.
DATA_CONTAINERS = ("postgres", "postgres_replica", "rabbitmq", "postgres_backup")


def resource_block(text: str, kind: str, name: str) -> str | None:
    """Тело ресурса до закрывающей скобки в первой колонке."""
    match = re.search(
        rf"^resource\s+\"{kind}\"\s+\"{name}\"\s*\{{(.*?)^\}}",
        text,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else None


def container_networks(text: str, name: str) -> frozenset[str] | None:
    """Ресурсы docker_network, к которым подключён контейнер."""
    block = resource_block(text, "docker_container", name)
    if block is None:
        return None
    return frozenset(
        re.findall(
            r"networks_advanced\s*\{\s*name\s*=\s*docker_network\.(\w+)\.name", block
        )
    )


def container_publishes_ports(text: str, name: str) -> bool | None:
    block = resource_block(text, "docker_container", name)
    if block is None:
        return None
    return bool(
        re.search(r"^\s*(?:dynamic\s+\"ports\"|ports)\s*\{", block, re.MULTILINE)
    )


def network_is_internal(text: str, name: str) -> bool | None:
    block = resource_block(text, "docker_network", name)
    if block is None:
        return None
    return bool(re.search(r"^\s*internal\s*=\s*true\b", block, re.MULTILINE))


def test_extractors_find_facts_that_are_present() -> None:
    sample = (
        'resource "docker_network" "backend" {\n'
        '  name     = "dmc268-backend"\n'
        "  internal = true\n"
        "}\n"
        'resource "docker_container" "postgres" {\n'
        "  # порт убран\n"
        "  networks_advanced {\n"
        "    name = docker_network.backend.name\n"
        "  }\n"
        "  ports {\n"
        "    internal = 5432\n"
        "  }\n"
        "}\n"
    )
    assert network_is_internal(sample, "backend") is True
    assert container_networks(sample, "postgres") == frozenset({"backend"})
    assert container_publishes_ports(sample, "postgres") is True


def test_extractors_report_absence_instead_of_a_blank() -> None:
    assert resource_block("ничего похожего", "docker_container", "postgres") is None
    assert container_networks("ничего похожего", "postgres") is None
    assert container_publishes_ports("ничего похожего", "postgres") is None
    assert network_is_internal("ничего похожего", "backend") is None


def test_data_network_has_no_way_out() -> None:
    assert network_is_internal(INFRA.read_text(), "backend") is True, (
        "сеть данных dmc268-backend должна быть internal: без этого база и брокер "
        "получают выход в интернет"
    )


def test_data_containers_live_only_in_the_data_network() -> None:
    text = INFRA.read_text()
    for name in DATA_CONTAINERS:
        networks = container_networks(text, name)
        assert networks is not None, f"контейнер {name} не найден в infra/main.tf"
        assert networks == frozenset({"backend"}), (
            f"{name} подключён к {sorted(networks)}: контейнеры с данными — только "
            "во внутренней сети, иначе их видит интерфейс"
        )


def test_data_containers_publish_no_host_ports() -> None:
    text = INFRA.read_text()
    for name in DATA_CONTAINERS:
        publishes = container_publishes_ports(text, name)
        assert publishes is not None, f"контейнер {name} не найден в infra/main.tf"
        assert not publishes, f"{name} публикует порт на хосте"


def test_only_api_bridges_the_entry_and_data_networks() -> None:
    """Интерфейс подключается к сети `dmc268`; в ней из нашего стека — только API."""
    text = INFRA.read_text()
    containers = re.findall(
        r"^resource\s+\"docker_container\"\s+\"(\w+)\"", text, re.MULTILINE
    )
    assert containers, "контейнеры не найдены в infra/main.tf"
    in_entry = {
        name
        for name in containers
        if "dmc268" in (container_networks(text, name) or ())
    }
    assert in_entry == {"api"}, (
        f"в сети входа dmc268 кроме API: {sorted(in_entry - {'api'})}"
    )
