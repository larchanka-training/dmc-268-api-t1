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
NOT_A_NETWORK_REFERENCE = "<не ссылка на docker_network>"

# Контейнеры с данными: никаких портов на хосте.
DATA_CONTAINERS = ("postgres", "postgres_replica", "rabbitmq", "postgres_backup")

# Ровно эти сети у каждого контейнера стека (спека `backend-delivery`, «Сети
# стека разделены по назначению»). Новый контейнер обязан попасть сюда — иначе
# его сети никто не сверит.
EXPECTED_NETWORKS: dict[str, frozenset[str]] = {
    "postgres": frozenset({"backend"}),
    "postgres_replica": frozenset({"backend"}),
    "postgres_backup": frozenset({"backend"}),
    "rabbitmq": frozenset({"backend"}),
    "migrate": frozenset({"backend"}),
    "replication_setup": frozenset({"backend"}),
    "api": frozenset({"dmc268", "backend"}),
    "worker": frozenset({"backend", "egress"}),
}


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
    networks: set[str] = set()
    # Блок целиком, а не «name сразу после скобки»: поле name может идти и после
    # aliases или ipv4_address.
    for body in re.findall(r"networks_advanced\s*\{([^{}]*)\}", block):
        ref = re.search(r"\bname\s*=\s*docker_network\.(\w+)\.name", body)
        networks.add(ref.group(1) if ref else NOT_A_NETWORK_REFERENCE)
    return frozenset(networks)


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
        "  networks_advanced {\n"
        '    aliases = ["db"]\n'
        "    name    = docker_network.egress.name\n"
        "  }\n"
        "  ports {\n"
        "    internal = 5432\n"
        "  }\n"
        "}\n"
    )
    assert network_is_internal(sample, "backend") is True
    assert container_networks(sample, "postgres") == frozenset({"backend", "egress"})
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


def test_data_containers_publish_no_host_ports() -> None:
    text = INFRA.read_text()
    for name in DATA_CONTAINERS:
        publishes = container_publishes_ports(text, name)
        assert publishes is not None, f"контейнер {name} не найден в infra/main.tf"
        assert not publishes, f"{name} публикует порт на хосте"


def test_every_container_is_in_exactly_its_networks() -> None:
    """Сети каждого контейнера — ровно ожидаемые, без лишних и недостающих.

    В том числе: в сети входа `dmc268`, к которой подключается интерфейс, из
    нашего стека только API, а воркер ходит наружу через `egress`.
    """
    text = INFRA.read_text()
    containers = set(
        re.findall(r"^resource\s+\"docker_container\"\s+\"(\w+)\"", text, re.MULTILINE)
    )
    assert containers, "контейнеры не найдены в infra/main.tf"
    assert containers == set(EXPECTED_NETWORKS), (
        f"контейнеры в infra/main.tf и в EXPECTED_NETWORKS разошлись: "
        f"нет в тесте {sorted(containers - set(EXPECTED_NETWORKS))}, "
        f"нет в main.tf {sorted(set(EXPECTED_NETWORKS) - containers)}"
    )
    for name, expected in EXPECTED_NETWORKS.items():
        networks = container_networks(text, name)
        assert networks == expected, (
            f"{name}: сети {sorted(networks or ())}, ожидались {sorted(expected)}"
        )
