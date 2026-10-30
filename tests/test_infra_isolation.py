"""Изоляция серверного стека — по описанию `infra/*.tf`.

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

from tests.infra_text import read_infra_text
from tests.shell_text import commands

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
    "replication_setup": frozenset({"backend"}),
    "api": frozenset({"dmc268", "backend"}),
    "worker": frozenset({"backend", "egress"}),
    # Стек наблюдаемости: цели receiver-ов и OTLP — в сети данных, остальное
    # — в своей сети. В dmc268 (там UI) из стека не ходит никто: трейсы
    # браузера коллектор принимает из отдельной internal-сети dmc268-otlp, куда
    # подключается и интерфейс. Grafana — ещё и в отдельной сети dmc268-grafana:
    # порты на internal-сети Docker не публикует, поэтому порт Grafana живёт в
    # не-internal сети. В сети socket-proxy — только proxy и экземпляр
    # коллектора с receiver docker_stats (почему — infra/README.md, «Метрики контейнеров»).
    "otelcol": frozenset({"backend", "observability", "otlp"}),
    "otelcol_docker": frozenset({"observability", "observability_docker_proxy"}),
    "loki": frozenset({"observability"}),
    "tempo": frozenset({"observability"}),
    "prometheus": frozenset({"backend", "observability"}),
    "grafana": frozenset({"observability", "grafana"}),
    "node_exporter": frozenset({"observability"}),
    "docker_socket_proxy": frozenset({"observability_docker_proxy"}),
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
    # aliases или ipv4_address. Индекс [0] — у count-ресурсов стека
    # наблюдаемости (count = 1 : 0 за observability_enabled).
    for body in re.findall(r"networks_advanced\s*\{([^{}]*)\}", block):
        ref = re.search(r"\bname\s*=\s*docker_network\.(\w+)(?:\[\d+\])?\.name", body)
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
        "  networks_advanced {\n"
        "    name = docker_network.observability[0].name\n"
        "  }\n"
        "  ports {\n"
        "    internal = 5432\n"
        "  }\n"
        "}\n"
    )
    assert network_is_internal(sample, "backend") is True
    assert container_networks(sample, "postgres") == frozenset(
        {"backend", "egress", "observability"}
    )
    assert container_publishes_ports(sample, "postgres") is True


def test_extractors_report_absence_instead_of_a_blank() -> None:
    assert resource_block("ничего похожего", "docker_container", "postgres") is None
    assert container_networks("ничего похожего", "postgres") is None
    assert container_publishes_ports("ничего похожего", "postgres") is None
    assert network_is_internal("ничего похожего", "backend") is None


def test_data_network_has_no_way_out() -> None:
    assert network_is_internal(read_infra_text(), "backend") is True, (
        "сеть данных dmc268-backend должна быть internal: без этого база и брокер "
        "получают выход в интернет"
    )


def test_observability_network_has_no_way_out() -> None:
    """Сеть стека наблюдаемости internal, как и сеть данных: наружу из стека
    не ходит никто. Сеть публикации порта Grafana dmc268-grafana — не
    internal (порты на internal-сети Docker не публикует): проверяем именно
    отсутствие internal = true — его постановка сломала бы публикацию порта.
    """
    text = read_infra_text()
    assert network_is_internal(text, "observability") is True, (
        "сеть стека dmc268-observability должна быть internal: без этого стек "
        "получает выход в интернет"
    )
    assert network_is_internal(text, "grafana") is False, (
        "сеть dmc268-grafana не должна быть internal: её единственное назначение — "
        "публикуемый порт Grafana, а порты на internal-сети Docker не публикует"
    )


def test_browser_otlp_network_has_no_way_out() -> None:
    assert network_is_internal(read_infra_text(), "otlp") is True, (
        "сеть dmc268-otlp должна быть internal: через неё интерфейс ходит в коллектор, "
        "а без internal коллектор получает выход в интернет"
    )


def test_data_containers_publish_no_host_ports() -> None:
    text = read_infra_text()
    for name in DATA_CONTAINERS:
        publishes = container_publishes_ports(text, name)
        assert publishes is not None, f"контейнер {name} не найден в infra/*.tf"
        assert not publishes, f"{name} публикует порт на хосте"


def test_every_container_is_in_exactly_its_networks() -> None:
    """Сети каждого контейнера — ровно ожидаемые, без лишних и недостающих.

    В том числе: в сети входа `dmc268`, к которой подключается интерфейс, из
    нашего стека только API, а воркер ходит наружу через `egress`.
    """
    text = read_infra_text()
    containers = set(
        re.findall(r"^resource\s+\"docker_container\"\s+\"(\w+)\"", text, re.MULTILINE)
    )
    assert containers, "контейнеры не найдены в infra/*.tf"
    assert containers == set(EXPECTED_NETWORKS), (
        f"контейнеры в infra/*.tf и в EXPECTED_NETWORKS разошлись: "
        f"нет в тесте {sorted(containers - set(EXPECTED_NETWORKS))}, "
        f"нет в infra/*.tf {sorted(set(EXPECTED_NETWORKS) - containers)}"
    )
    for name, expected in EXPECTED_NETWORKS.items():
        networks = container_networks(text, name)
        assert networks == expected, (
            f"{name}: сети {sorted(networks or ())}, ожидались {sorted(expected)}"
        )


MIGRATE_SCRIPT = Path(".github/scripts/deploy/migrate.sh")


def migrate_networks(script: str) -> list[str]:
    """Сети всех `docker run` миграций в шаге деплоя, по одной на каждый `--network`.

    Все, а не первая (ревью #220): `alembic current` после сбоя однажды остался
    в сети `dmc268`, где базы нет, а проверка первого совпадения была зелёной.
    Общие аргументы из массива bash подставляются в каждый запуск, комментарии
    не считаются.
    """
    return re.findall(r"(?<!\S)--network[ =](\S+)", commands(script))


def test_migrate_network_extractor() -> None:
    assert migrate_networks("docker run --rm \\\n  --network dmc268-backend \\\n") == ["dmc268-backend"]
    assert migrate_networks("docker run --rm image") == []
    # Второй запуск в другой сети виден, а не прячется за первым.
    assert migrate_networks(
        "docker run --rm \\\n  --network dmc268-backend \\\n  img\n"
        "timeout 60 docker run --rm --network dmc268 img\n"
    ) == ["dmc268-backend", "dmc268"]
    # Из массива — в каждый запуск, и добавка к одному из них тоже видна.
    assert migrate_networks(
        "run=(\n  docker run --rm\n  --network dmc268-backend\n)\n"
        '"${run[@]}" img a\ntimeout 60 "${run[@]}" --network dmc268 img b\n'
    ) == ["dmc268-backend", "dmc268-backend", "dmc268"]
    assert migrate_networks("# --network dmc268 — так было до #102\n") == []


def network_name(text: str, resource: str) -> str | None:
    """Имя docker-сети (`name = "…"`) у ресурса `docker_network.<resource>`."""
    block = resource_block(text, "docker_network", resource)
    if block is None:
        return None
    match = re.search(r'^\s*name\s*=\s*"([^"]+)"', block, re.MULTILINE)
    return match.group(1) if match else None


def test_network_name_extractor() -> None:
    sample = 'resource "docker_network" "backend" {\n  name     = "dmc268-backend"\n  internal = true\n}\n'
    assert network_name(sample, "backend") == "dmc268-backend"
    assert network_name(sample, "egress") is None


def test_migrations_run_in_the_database_network() -> None:
    """Миграции — шаг деплоя, не контейнер стека: их сеть Terraform не видит.

    Сверяется с фактической сетью postgres из `infra/*.tf`, а не со строкой:
    переедет база — тест покажет, что миграции остались в старой сети, и деплой
    не упадёт уже на сервере с `could not translate host name dmc268-postgres`.
    """
    text = read_infra_text()
    postgres_networks = container_networks(text, "postgres")
    assert postgres_networks, "сети postgres не найдены в infra/*.tf"
    names = {network_name(text, network) for network in postgres_networks}
    assert None not in names, (
        f"не найдены имена сетей {sorted(postgres_networks)} в infra/*.tf"
    )
    script = MIGRATE_SCRIPT.read_text()
    used = migrate_networks(script)
    runs = re.findall(r"(?<!\S)docker run\b", commands(script))
    # Каждый запуск — с сетью, и каждая сеть — сеть базы.
    assert used and len(used) == len(runs), f"{MIGRATE_SCRIPT}: сети {used} на {len(runs)} запусков docker run"
    assert set(used) <= names, (
        f"{MIGRATE_SCRIPT}: миграции в сетях {sorted(set(used))}, а база — в {sorted(n for n in names if n)}"
    )
