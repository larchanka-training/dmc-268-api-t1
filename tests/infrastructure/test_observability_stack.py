"""Структура стека наблюдаемости без Docker: compose и infra описывают одно.

Правки конфигов не поднимают стек, а рассинхрон compose и infra/ обнаружился
бы только на деплое. Тесты держат структуру: образы стека зафиксированы
дайджестами одинаково в обоих местах, отдельного экспортёра Postgres больше
нет (метрики базы снимает receiver postgresql коллектора, решение #89),
сервер и контейнеры видны (node-exporter и receiver docker_stats отдельного
экземпляра коллектора — без docker.sock, через socket-proxy), у Prometheus есть потолок по размеру,
имена compose не сталкиваются с infra на общем хосте.
Валидацию конфига самим коллектором делает класс
TestCollectorConfigValidation в test_collector_redaction.py.
"""

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO / "docker-compose.yml"
LOCALS_TF = REPO / "infra" / "locals.tf"
OBSERVABILITY_TF = REPO / "infra" / "observability.tf"
OUTPUTS_TF = REPO / "infra" / "outputs.tf"
MIGRATE_SCRIPT = REPO / ".github" / "scripts" / "deploy" / "migrate.sh"
VARIABLES_TF = REPO / "infra" / "variables.tf"
OBSERVABILITY_DIR = REPO / "infra" / "observability"
PROMETHEUS_CONFIG = OBSERVABILITY_DIR / "prometheus.yml"
LOKI_CONFIG = OBSERVABILITY_DIR / "loki.yaml"
TEMPO_CONFIG = OBSERVABILITY_DIR / "tempo.yaml"
README = REPO / "infra" / "README.md"
DASHBOARDS_DIR = REPO / "infra" / "observability" / "grafana" / "provisioning" / "dashboards"
PROVIDER_CONFIG = DASHBOARDS_DIR / "dashboards.yaml"
DASHBOARD_JSON = DASHBOARDS_DIR / "dmc268-overview.json"

# Сервисы стека наблюдаемости и их переменные-образы в locals.tf:
# пар «сервис compose → local» обязан совпадать дайджестами в обоих файлах.
STACK_SERVICES = {
    "collector": "otelcol_image",
    "collector-docker": "otelcol_image",
    "collector-logs": "otelcol_image",
    "loki": "loki_image",
    "tempo": "tempo_image",
    "prometheus": "prometheus_image",
    "grafana": "grafana_image",
    "node-exporter": "node_exporter_image",
    "dockerproxy": "docker_socket_proxy_image",
}

# имя@sha256:… — дайджест манифест-листа (multi-arch), а не платформенный.
_DIGEST = re.compile(r"^(?P<name>[^@\s]+)@sha256:(?P<digest>[0-9a-f]{64})$")


COMPOSE_LOGS_FILE = OBSERVABILITY_DIR / "compose.logs.yml"


def load_compose() -> dict[str, Any]:
    compose: dict[str, Any] = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    return compose


def load_prometheus() -> dict[str, Any]:
    config: dict[str, Any] = yaml.safe_load(PROMETHEUS_CONFIG.read_text(encoding="utf-8"))
    return config


def load_loki() -> dict[str, Any]:
    config: dict[str, Any] = yaml.safe_load(LOKI_CONFIG.read_text(encoding="utf-8"))
    return config


def parse_tf_locals() -> dict[str, str]:
    """Переменные locals (infra/locals.tf): имя → значение-строка."""
    locals: dict[str, str] = {}
    for name, value in re.findall(r'^\s*(\w+)\s+=\s+"([^"]*)"', LOCALS_TF.read_text(encoding="utf-8"), re.MULTILINE):
        locals[name] = value
    return locals


def _tf_container_block(name: str) -> str:
    """Текст блока docker_container в infra/*.tf по имени ресурса.

    Конфигурация разрезана по доменам (app.tf, postgres.tf, observability.tf),
    поэтому поиск идёт по всем файлам infra/*.tf. Вложенные блоки сдвинуты
    отступом, поэтому конец ресурса — первая закрывающая скобка в начале
    строки после заголовка.
    """
    for path in sorted((REPO / "infra").glob("*.tf")):
        match = re.search(
            rf'resource "docker_container" "{name}" \{{.*?\n\}}',
            path.read_text(encoding="utf-8"),
            re.DOTALL,
        )
        if match is not None:
            return match.group(0)
    raise AssertionError(f"нет docker_container.{name} в infra/*.tf")


# --- postgres-exporter удалён целиком -----------------------------------------------


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        (COMPOSE_FILE, "postgres-exporter"),
        (OBSERVABILITY_TF, "postgres_exporter"),
        (VARIABLES_TF, "postgres_exporter"),
        (README, "postgres-exporter"),
    ],
)
def test_postgres_exporter_is_gone(path: Path, needle: str) -> None:
    """Экспортёра Postgres нет нигде: его заменил receiver postgresql (решение #89)."""
    assert needle not in path.read_text(encoding="utf-8")


def test_prometheus_has_no_postgres_job() -> None:
    """Job postgres в prometheus.yml удалён вместе с экспортёром."""
    jobs = {job["job_name"] for job in load_prometheus()["scrape_configs"]}
    assert "postgres" not in jobs


# --- node-exporter и socket-proxy в обоих окружениях ------------------------------------


@pytest.mark.parametrize("service", ["node-exporter", "dockerproxy"])
def test_host_and_container_exporters_in_compose(service: str) -> None:
    """Сервер, контейнеры и прокси Docker API — в профиле observability, без портов наружу."""
    service_config = load_compose()["services"][service]
    assert "observability" in service_config["profiles"]


@pytest.mark.parametrize("container", ["node_exporter", "docker_socket_proxy"])
def test_host_and_container_exporters_in_infra(container: str) -> None:
    """То же в infra: контейнер, образ из locals и потолок памяти из переменной."""
    text = OBSERVABILITY_TF.read_text(encoding="utf-8")
    assert f'resource "docker_image" "{container}"' in text
    assert f'resource "docker_container" "{container}"' in text
    memory_variable = container.replace("docker_socket_proxy", "docker_proxy")
    assert f"var.observability_memory_mb.{memory_variable}" in text


@pytest.mark.parametrize(
    ("job", "target"),
    [
        ("node-exporter", "node-exporter:9100"),
        ("docker-stats", "collector-docker:8889"),
        ("collector-self", "collector:8888"),
        ("collector-docker-self", "collector-docker:8888"),
        ("collector-logs-self", "collector-logs:8888"),
        ("loki", "loki:3100"),
        ("tempo", "tempo:3200"),
    ],
)
def test_prometheus_scrapes_exporters_by_service_hosts(job: str, target: str) -> None:
    """Job по хостнейму сервиса и его порту."""
    jobs = {j["job_name"]: j for j in load_prometheus()["scrape_configs"]}
    assert jobs[job]["static_configs"][0]["targets"] == [target]


@pytest.mark.parametrize("config", ["otelcol.yaml", "otelcol-docker.yaml", "otelcol-logs.yaml"])
def test_collector_serves_own_metrics_beyond_localhost(config: str) -> None:
    """По умолчанию свои метрики коллектор отдаёт только на 127.0.0.1 — Prometheus их не видит."""
    collector = yaml.safe_load((OBSERVABILITY_DIR / config).read_text(encoding="utf-8"))
    (reader,) = collector["service"]["telemetry"]["metrics"]["readers"]
    assert reader["pull"]["exporter"]["prometheus"] == {"host": "0.0.0.0", "port": 8888}


def test_rabbitmq_job_scrapes_per_object_metrics() -> None:
    """RabbitMQ скреится с per-object эндпоинта: агрегированный /metrics
    теряет лейбл queue, по которому панель очереди фильтрует review_jobs.qq."""
    jobs = {job["job_name"]: job for job in load_prometheus()["scrape_configs"]}
    assert jobs["rabbitmq"]["metrics_path"] == "/metrics/per-object"
    assert jobs["rabbitmq"]["static_configs"][0]["targets"] == ["dmc268-rabbitmq:15692"]


# --- дайджесты образов: compose и infra совпадают ------------------------------------


def _compose_digests() -> dict[str, str]:
    services = load_compose()["services"]
    digests: dict[str, str] = {}
    for service in STACK_SERVICES:
        match = _DIGEST.match(services[service]["image"])
        assert match is not None, f"образ {service} не зафиксирован дайджестом"
        digests[service] = match.group("digest")
    return digests


def _infra_digests() -> dict[str, str]:
    locals = parse_tf_locals()
    digests: dict[str, str] = {}
    for service, local_name in STACK_SERVICES.items():
        match = _DIGEST.match(locals[local_name])
        assert match is not None, f"образ {local_name} не зафиксирован дайджестом"
        digests[service] = match.group("digest")
    return digests


def test_stack_images_are_pinned_by_digest_in_both_places() -> None:
    """Все образы стека — имя@sha256:…, одинаковые в compose и infra.

    Тег плавает, дайджест манифест-листа нет: рассинхрон между окружениями —
    ошибка, поэтому сравниваются именно дайджесты, а не полные ссылки.
    """
    assert _compose_digests() == _infra_digests()


# --- service_version: тег из ссылки на образ ----------------------------------


def _service_version(image: str) -> str:
    """Прогнать locals `service_version` из locals.tf: те же regex в том же
    порядке `try`, что и в конфиге."""
    text = LOCALS_TF.read_text(encoding="utf-8")
    patterns = re.findall(r'regex\("([^"]+)", var\.api_image\)', text)
    assert patterns, "в locals.tf нет regex для service_version"
    for pattern in patterns:
        match = re.search(pattern, image)
        if match is not None:
            return match["tag"]
    return "unknown"


@pytest.mark.parametrize(
    ("image", "version"),
    [
        ("localhost:5000/dmc268-api:sha-abc123", "sha-abc123"),
        ("dmc268-api:sha-abc123@sha256:" + "0" * 64, "sha-abc123"),
        ("ghcr.io/larchanka/dmc268-api:sha-def456@sha256:" + "a" * 64, "sha-def456"),
    ],
)
def test_service_version_extracts_the_tag_before_the_digest(image: str, version: str) -> None:
    """Тег — между последним двоеточием и концом или «@дайджестом»: формат
    образа с дайджестом (после #204) не должен отдавать hex вместо тега
    (замечание ревью PR #130)."""
    assert _service_version(image) == version


def test_stack_published_ports_match_between_compose_and_infra() -> None:
    """Публикуемые порты стека одинаковы в compose и infra.

    Публикуемых портов стека два — Grafana (3000 на bind_ip) и приём fluentd
    коллектора логов (24224 на 127.0.0.1, его адрес — в опциях драйвера):
    остальной трафик внутренний, его публикует только человек, который правит
    оба файла. Рассинхрон порта молчалив так же, как рассинхрон дайджестов
    (замечание ревью PR #130), поэтому сверяются и внутренний порт, и дефолт
    внешней переменной.
    """
    assert load_compose()["services"]["grafana"]["ports"] == ["127.0.0.1:3000:3000"]

    match = re.search(
        r"ports \{\s*internal = (\d+)\s*external = var\.(\w+)\s*ip",
        _tf_container_block("grafana"),
    )
    assert match is not None, "у grafana в infra/*.tf нет блока ports"
    internal, external_var = int(match.group(1)), match.group(2)
    assert internal == 3000

    default = re.search(
        rf'variable "{external_var}" \{{.*?default\s*=\s*(\d+)',
        VARIABLES_TF.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert default is not None, f"у переменной {external_var} нет дефолта"
    assert int(default.group(1)) == 3000

    assert load_compose()["services"]["collector-logs"]["ports"] == ["127.0.0.1:24224:24224"]
    assert re.search(
        r'ports \{\s*internal = 24224\s*external = 24224\s*ip\s*= "127\.0\.0\.1"',
        _tf_container_block("otelcol_logs"),
    )


# --- имена: алиасы вместо container_name, том grafana_data без name -----------------


@pytest.mark.parametrize(
    ("service", "alias"),
    [("postgres", "dmc268-postgres"), ("rabbitmq", "dmc268-rabbitmq")],
)
def test_base_services_resolve_by_network_alias(service: str, alias: str) -> None:
    """Хосты конфигов резолвятся алиасом сети, не фиксированным именем контейнера.

    container_name сталкивается с infra на общем хосте; алиас в default-сети
    compose — нет.
    """
    service_config = load_compose()["services"][service]
    assert "container_name" not in service_config
    assert alias in service_config["networks"]["default"]["aliases"]


def test_stack_data_volumes_have_no_fixed_name() -> None:
    """Ни у одного тома данных стека нет фиксированного name: том получает
    префикс проекта compose и не пересекается с томами infra — иначе compose и
    локальный `tofu apply` с флагом (второй worktree) писали бы в ОДИН том
    dmc268-*-data двумя процессами в одну TSDB (замечание ревью PR #130)."""

    # Том без отображения yaml разбирает в None — ключей у него нет.
    volumes = load_compose()["volumes"]
    for name in ("loki_data", "tempo_data", "prometheus_data", "grafana_data"):
        assert not (volumes[name] or {}).get("name"), name


# --- лимиты и preload: одно и то же в compose и infra -------------------------------


@pytest.mark.parametrize("file", [COMPOSE_FILE, OBSERVABILITY_TF])
def test_prometheus_is_capped_by_size(file: Path) -> None:
    """Потолок по размеру рядом с потолком по времени: всплеск кардинальности
    не должен забить диск раньше срока хранения."""
    assert "--storage.tsdb.retention.size=8GB" in file.read_text(encoding="utf-8")


def test_compose_preloads_pg_stat_statements_always() -> None:
    """Локальный стек держит preload pg_stat_statements всегда: расширение —
    для ручной диагностики медленных запросов (создаёт миграция 0013),
    накладные расходы preload ничтожны, переключателя в compose нет.
    track_utility=off рядом: утилитные выражения (`ALTER ROLE … PASSWORD`)
    не попадают в pg_stat_statements, который читает роль с pg_monitor."""
    services = load_compose()["services"]
    assert services["postgres"]["command"] == [
        "postgres",
        "-c", "shared_preload_libraries=pg_stat_statements",
        "-c", "pg_stat_statements.track_utility=off",
    ]


def test_infra_preloads_pg_stat_statements_only_with_the_stack() -> None:
    """На стенде preload включается вместе со стеком наблюдаемости
    (observability_enabled): флаг живёт в условной ветке command, при
    выключенном стеке команда без него. Безусловная часть command
    (#102) — hba_file и потолок WAL слота реплики. track_utility=off рядом
    с preload сверяет по плану tftest `postgres_command_tracks_the_stack_flag`
    (infra/tests/observability.tftest.hcl)."""
    block = _tf_container_block("postgres")
    assert "command = concat([" in block
    assert '"-c", "hba_file=/etc/postgresql/pg_hba.conf",' in block
    assert 'var.observability_enabled ? [' in block
    assert '"-c", "shared_preload_libraries=pg_stat_statements",' in block


# --- метрики контейнеров: docker_stats коллектора через socket-proxy -----------------


def test_container_metrics_come_from_a_separate_collector_through_the_proxy() -> None:
    """Метрики контейнеров снимает отдельный экземпляр коллектора с одним
    receiver docker_stats через socket-proxy (почему не cAdvisor — #249).
    Входящего OTLP у него нет, а у основного коллектора нет docker_stats:
    inspect через proxy отдаёт Env всех контейнеров."""
    docker = yaml.safe_load((OBSERVABILITY_DIR / "otelcol-docker.yaml").read_text(encoding="utf-8"))
    assert set(docker["receivers"]) == {"docker_stats"}
    assert docker["receivers"]["docker_stats"]["endpoint"] == "tcp://dockerproxy:2375"
    # Опечатку в имени атрибута коллектор не отвергает: id, hostname и образ меняются каждым деплоем.
    assert docker["receivers"]["docker_stats"]["resource_attributes"] == {
        name: {"enabled": False} for name in ("container.id", "container.hostname", "container.image.name")
    }
    assert docker["service"]["pipelines"]["metrics"]["receivers"] == ["docker_stats"]

    main = yaml.safe_load((OBSERVABILITY_DIR / "otelcol.yaml").read_text(encoding="utf-8"))
    assert "docker_stats" not in main["receivers"]
    assert "dockerproxy" not in json.dumps(main)

    compose = load_compose()["services"]["collector-docker"]
    assert "./infra/observability/otelcol-docker.yaml:/etc/otelcol-contrib/config.yaml:ro" in compose["volumes"]
    assert 'file("${path.module}/observability/otelcol-docker.yaml")' in _tf_container_block("otelcol_docker")


# --- логи контейнеров: драйвер fluentd и коллектор логов ---------------------------


def test_container_logs_go_through_the_logs_collector() -> None:
    """Логи postgres, rabbitmq и реплики шлёт драйвер fluentd в коллектор логов;
    в compose опции те же, что local.fluentd_log_opts. Почему драйвер в отдельном
    файле — infra/README.md, «Логи контейнеров»."""
    services = load_compose()["services"]
    overrides = yaml.safe_load(COMPOSE_LOGS_FILE.read_text(encoding="utf-8"))["services"]
    tf_block = re.search(r"fluentd_log_opts = \{(.*?)\n  \}", LOCALS_TF.read_text(encoding="utf-8"), re.DOTALL)
    assert tf_block is not None
    tf_opts = dict(re.findall(r'"([\w-]+)"\s*=\s*"([^"]*)"', tf_block.group(1)))
    assert tf_opts.pop("tag") == "{{.Name}}"
    for service in ("postgres", "rabbitmq"):
        assert "logging" not in services[service], service
        assert overrides[service]["logging"] == {
            "driver": "fluentd",
            # В compose нет container_name: тег — имя контейнера на стенде.
            "options": tf_opts | {"tag": f"dmc268-{service}"},
        }, service
        block = _tf_container_block(service)
        assert 'log_driver = var.observability_enabled ? "fluentd" : "json-file"' in block, service
        assert "log_opts   = var.observability_enabled ? local.fluentd_log_opts : local.log_opts" in block, service

    # Реплики в compose нет, на стенде она пишет так же.
    replica = _tf_container_block("postgres_replica")
    assert 'log_driver = var.observability_enabled ? "fluentd" : "json-file"' in replica
    assert "log_opts   = var.observability_enabled ? local.fluentd_log_opts : local.log_opts" in replica

    logs = yaml.safe_load((OBSERVABILITY_DIR / "otelcol-logs.yaml").read_text(encoding="utf-8"))
    assert set(logs["receivers"]) == {"fluent_forward"}
    assert logs["exporters"] == {"otlp_http/collector": {"endpoint": "http://collector:4318"}}
    compose = services["collector-logs"]
    assert "./infra/observability/otelcol-logs.yaml:/etc/otelcol-contrib/config.yaml:ro" in compose["volumes"]
    assert 'file("${path.module}/observability/otelcol-logs.yaml")' in _tf_container_block("otelcol_logs")


SOCKET_PROXY_TEMPLATE = OBSERVABILITY_DIR / "docker-socket-proxy.cfg"
SOCKET_PROXY_TEMPLATE_PATH = "/usr/local/etc/haproxy/haproxy.cfg.template"


def test_socket_proxy_allows_only_the_docker_stats_paths() -> None:
    """Белый список путей proxy живёт в шаблоне haproxy, а не в env образа.

    CONTAINERS=1 у tecnativa/docker-socket-proxy открывал ЛЮБОЙ GET под
    /containers — /containers/{id}/archive?path=… (любой файл любого
    контейнера, включая том Postgres), /export и inspect с полным Env
    (замечание ревью PR #130). Свой шаблон разрешает только то, что берёт
    receiver docker_stats: список, inspect, статистику и события. Шаблон один
    и тот же в обоих окружениях: compose монтирует файл по пути, который
    рендерит entrypoint образа, infra заливает его upload-ом по тому же пути.
    """
    proxy = load_compose()["services"]["dockerproxy"]
    assert "CONTAINERS" not in (proxy.get("environment") or {})
    assert "INFO" not in (proxy.get("environment") or {})
    assert (
        f"./infra/observability/docker-socket-proxy.cfg:{SOCKET_PROXY_TEMPLATE_PATH}:ro"
        in proxy["volumes"]
    )

    infra_proxy = _tf_container_block("docker_socket_proxy")
    assert "CONTAINERS=" not in infra_proxy and "INFO=" not in infra_proxy
    assert 'content = file("${path.module}/observability/docker-socket-proxy.cfg")' in infra_proxy
    assert f'file    = "{SOCKET_PROXY_TEMPLATE_PATH}"' in infra_proxy

    assert SOCKET_PROXY_TEMPLATE.is_file(), "нет infra/observability/docker-socket-proxy.cfg"
    template = SOCKET_PROXY_TEMPLATE.read_text(encoding="utf-8")
    allow_lines = [line.strip() for line in template.splitlines() if "http-request allow" in line]
    assert len(allow_lines) == 1
    # Всё, что не в списке, — deny: и не-GET, и остальные пути.
    assert "http-request deny unless METH_GET" in template
    assert "    http-request deny\n    default_backend dockerbackend" in template


def _socket_proxy_allows(url: str) -> bool:
    """Пропустит ли proxy GET на url: регулярка allow-строки шаблона против
    пути без query, раскодированного, как `path,url_dec` у haproxy (`-i` —
    без учёта регистра)."""
    template = SOCKET_PROXY_TEMPLATE.read_text(encoding="utf-8")
    match = re.search(r"http-request allow if \{ path,url_dec -m reg -i (\S+) \}", template)
    assert match is not None, "нет allow-строки в docker-socket-proxy.cfg"
    return re.fullmatch(match.group(1), unquote(urlsplit(url).path), re.IGNORECASE) is not None


@pytest.mark.parametrize(
    "url",
    [
        "/containers/json",
        "/v1.47/containers/json",
        "/containers/abc/json",
        "/v1.47/containers/abc/stats",
        "/v1.47/containers/abc/stats?stream=false",
        "/events",
        "/v1.47/events?filters=%7B%7D",
        "/info",
        "/version",
        "/_ping",
    ],
)
def test_socket_proxy_lets_docker_stats_paths_through(url: str) -> None:
    assert _socket_proxy_allows(url)


@pytest.mark.parametrize(
    "url",
    [
        "/containers/abc/logs",
        "/containers/abc/archive",
        "/containers/abc/export",
        "/exec/abc/json",
        "/containers/a/b/stats",
        # Слэш, закодированный в id, раскрывается до проверки.
        "/containers/a%2Fb/stats",
        "/v1.47/events/x",
        "/images/json",
        # Процессы с командными строками, список файлов, потоки stdio контейнера.
        "/containers/abc/top",
        "/containers/abc/changes",
        "/containers/abc/attach/ws",
        "/volumes",
        "/system/df",
    ],
)
def test_socket_proxy_rejects_other_paths(url: str) -> None:
    assert not _socket_proxy_allows(url)


def test_docker_socket_is_mounted_only_to_the_proxy() -> None:
    """docker.sock — только у proxy, в compose и в infra; коллектор ходит к
    Docker по TCP-адресу proxy (сети сверяют тесты сетей ниже)."""
    services = load_compose()["services"]
    for collector in ("collector", "collector-docker", "collector-logs"):
        assert not any("docker.sock" in volume for volume in services[collector]["volumes"])
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in services["dockerproxy"]["volumes"]
    assert "/var/run/docker.sock" not in _tf_container_block("otelcol")
    assert "/var/run/docker.sock" not in _tf_container_block("otelcol_docker")
    assert "/var/run/docker.sock" not in _tf_container_block("otelcol_logs")
    assert "/var/run/docker.sock" in _tf_container_block("docker_socket_proxy")


# --- сети: стек отдельно от приложения -------------------------------------------------


def test_stack_networks_split_the_stack_from_the_app_in_compose() -> None:
    """Сеть стека отдельна от default: из контейнеров интерфейса (в default)
    не видно Loki, Prometheus, Tempo и OTLP коллектора (замечание ревью
    PR #130). Коллектор и Prometheus двуподветочны (приём/скрейп rabbitmq),
    остальной стек — только в сети стека; сеть стека и docker_proxy —
    internal, а порт Grafana публикуется через отдельную не-internal сеть
    grafana, как на стенде."""
    networks = load_compose()["networks"]
    assert networks["docker_proxy"]["internal"] is True
    assert networks["observability"]["internal"] is True
    assert not (networks["grafana"] or {}).get("internal")
    assert not (networks["logs"] or {}).get("internal")

    services = load_compose()["services"]
    assert set(services["collector"]["networks"]) == {"default", "observability"}
    assert set(services["collector-docker"]["networks"]) == {"observability", "docker_proxy"}
    assert set(services["collector-logs"]["networks"]) == {"observability", "logs"}
    # В сети proxy — только он сам и экземпляр коллектора с docker_stats:
    # inspect отдаёт Env всех контейнеров.
    assert {name for name, service in services.items() if "docker_proxy" in (service.get("networks") or {})} == {
        "dockerproxy",
        "collector-docker",
    }
    assert set(services["prometheus"]["networks"]) == {"default", "observability"}
    assert set(services["grafana"]["networks"]) == {"observability", "grafana"}
    for service in ("loki", "tempo", "node-exporter"):
        assert set(services[service]["networks"]) == {"observability"}, service


def test_stack_networks_split_the_stack_from_the_app_in_infra() -> None:
    """То же в infra: у loki/tempo/grafana/node-exporter сети
    dmc268 нет; collector и prometheus — в данных (backend: postgres-
    receiver, скрейп rabbitmq) и сети стека; сеть стека internal, а порт
    Grafana публикуется через отдельную не-internal сеть dmc268-grafana:
    порты на internal-сети Docker не публикует (замечание ревью PR #130);
    docker_proxy — internal."""
    text = OBSERVABILITY_TF.read_text(encoding="utf-8")
    assert re.search(
        r'resource "docker_network" "observability" \{[^}]*internal\s*=\s*true', text
    )
    assert re.search(
        r'resource "docker_network" "observability_docker_proxy" \{[^}]*internal\s*=\s*true', text
    )
    # Сеть порта Grafana — ровно без internal: её постановка сломала бы
    # публикацию порта.
    grafana_network = re.search(
        r'resource "docker_network" "grafana" \{(.*?)^\}', text, re.DOTALL | re.MULTILINE
    )
    assert grafana_network is not None, "нет docker_network.grafana в observability.tf"
    assert not re.search(r"^\s*internal\s*=", grafana_network.group(1), re.MULTILINE)
    for name in ("loki", "tempo", "node_exporter"):
        block = _tf_container_block(name)
        assert "docker_network.dmc268" not in block, name
        assert "docker_network.observability[" in block, name
    grafana = _tf_container_block("grafana")
    assert "docker_network.dmc268" not in grafana
    assert "docker_network.observability[" in grafana
    assert "docker_network.grafana[0].name" in grafana
    for name in ("otelcol", "prometheus"):
        block = _tf_container_block(name)
        assert "docker_network.backend.name" in block, name
        assert "docker_network.observability[0].name" in block, name


# --- пароль роли мониторинга не теряется при переносе миграций ------------------------


def test_the_monitor_role_password_step_travels_with_the_migrations() -> None:
    """Шаг перегенерации пароля роли (ALTER ROLE при выключенном
    track_utility) и сам пароль едут вместе с миграциями в ОБОИХ окружениях:
    если при переносе миграций шаг потерять, ревизия 0013 создаст роль с
    дефолтным паролем и коллектор не войдёт (замечание ревью PR #130)."""
    compose_migrate = load_compose()["services"]["migrate"]
    assert "POSTGRES_MONITOR_PASSWORD" in compose_migrate["environment"]
    command = " ".join(compose_migrate["command"])
    assert "alembic upgrade head" in command
    assert "ALTER ROLE" in command
    assert "track_utility" in command

    script = MIGRATE_SCRIPT.read_text(encoding="utf-8")
    assert 'os.environ["POSTGRES_MONITOR_PASSWORD"]' in script
    assert "ALTER ROLE" in script
    assert "track_utility" in script

    # Пароль приезжает в скрипт окружением миграций — выходом migrate_env.
    outputs = OUTPUTS_TF.read_text(encoding="utf-8")
    assert "POSTGRES_MONITOR_PASSWORD=${var.postgres_monitor_password}" in outputs


# --- приём и телеметрия вендора -------------------------------------------------------


def test_collector_job_keeps_service_name_labels() -> None:
    """honor_labels у скрейп-джобы коллектора: exporter-порт отдаёт лейбл
    job = service.name, и без honor_labels Prometheus затер бы его именем
    джобы — панели дашборда, фильтрующие по job, остались бы пустыми."""
    job = {j["job_name"]: j for j in load_prometheus()["scrape_configs"]}["collector"]
    assert job["honor_labels"] is True


def test_loki_caps_ingestion() -> None:
    """Потолки приёма Loki: сбои SDK не должны забить Loki и диск; превышение
    Loki отвергает с 429, а не копит долг."""
    limits = load_loki()["limits_config"]
    assert limits["ingestion_rate_mb"] == 1
    assert limits["ingestion_burst_size_mb"] == 2
    assert limits["per_stream_rate_limit"] == "512KB"


def test_vendor_telemetry_is_off() -> None:
    """Аналитика вендоров выключена в обоих окружениях: Loki не отправляет
    статистику использования (analytics.reporting_enabled), Tempo — свой
    usage_report, Grafana — GF_ANALYTICS_* в compose и infra."""
    assert load_loki()["analytics"] == {"reporting_enabled": False}
    tempo = yaml.safe_load(TEMPO_CONFIG.read_text(encoding="utf-8"))
    assert tempo["usage_report"]["reporting_enabled"] is False
    grafana_compose = load_compose()["services"]["grafana"]["environment"]
    assert grafana_compose["GF_ANALYTICS_REPORTING_ENABLED"] == "false"
    assert grafana_compose["GF_ANALYTICS_CHECK_FOR_UPDATES"] == "false"
    assert grafana_compose["GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES"] == "false"
    observability_tf = OBSERVABILITY_TF.read_text(encoding="utf-8")
    assert '"GF_ANALYTICS_REPORTING_ENABLED=false"' in observability_tf
    assert '"GF_ANALYTICS_CHECK_FOR_UPDATES=false"' in observability_tf
    assert '"GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false"' in observability_tf


@pytest.mark.parametrize(
    ("path", "variables"),
    [
        (COMPOSE_FILE, ("POSTGRESQL_USERNAME", "POSTGRESQL_PASSWORD", "POSTGRESQL_ENDPOINT", "POSTGRESQL_DATABASE")),
        (OBSERVABILITY_TF, ("POSTGRESQL_USERNAME", "POSTGRESQL_PASSWORD", "POSTGRESQL_ENDPOINT", "POSTGRESQL_DATABASE")),
    ],
)
def test_collector_receives_postgresql_receiver_variables(path: Path, variables: tuple[str, ...]) -> None:
    """Реквизиты receiver postgresql приходят коллектору окружением в обоих местах."""
    text = path.read_text(encoding="utf-8")
    missing = [name for name in variables if name not in text]
    assert not missing, f"нет переменных receiver: {missing}"


# --- коллектор: логи только по OTLP, hardening как у api/worker ---------------------

# Значение env вида ${VAR:-default}: тест смотрит именно на дефолт.
_DEFAULT_ENV = re.compile(r"^\$\{[A-Z_]+:-(?P<default>.+)\}$")


def _compose_env_defaults(service: str) -> dict[str, str]:
    """Окружение сервиса compose: имя переменной → её дефолт.

    Значения вида ${VAR:-default} схлопываются до дефолта, литералы
    (DATABASE_URL и прочие реквизиты) остаются как есть.
    """
    environment = load_compose()["services"][service]["environment"]
    defaults: dict[str, str] = {}
    for name, value in environment.items():
        match = _DEFAULT_ENV.match(value)
        defaults[name] = match.group("default") if match else value
    return defaults


def test_collector_in_compose_takes_only_otlp_and_is_hardened() -> None:
    """Коллектор в compose принимает только OTLP: без файлов Docker и root.

    Логи приложения идут по OTLP из SDK (OTEL_LOGS_EXPORTER), поэтому маунт
    /var/lib/docker/containers, переменная OTELCOL_LOG_FILES, root и том под
    смещения file_storage ушли; вместо них — hardening api: без capabilities,
    файловая система только для чтения, /tmp — tmpfs.
    """
    collector = load_compose()["services"]["collector"]
    assert "user" not in collector
    assert "OTELCOL_LOG_FILES" not in collector["environment"]
    assert not any(
        mount.startswith("/var/lib/docker/containers") for mount in collector["volumes"]
    )
    assert collector["cap_drop"] == ["ALL"]
    assert collector["read_only"] is True
    assert "/tmp" in collector["tmpfs"]
    assert "collector_data" not in load_compose()["volumes"]


def test_collector_in_infra_takes_only_otlp_and_is_hardened() -> None:
    """То же в infra: без маунта лог-файлов, без root и с hardening api.

    Без read_only: конфиг заливается провайдером upload-ом (CopyToContainer,
    как docker cp) — в read-only rootfs демон отказывает в записи ещё до
    пути, apply упал бы на создании контейнера. Локальный коллектор
    read_only: конфиг приходит read-only маунтом."""
    block = _tf_container_block("otelcol")
    text = OBSERVABILITY_TF.read_text(encoding="utf-8")
    assert "/var/lib/docker/containers" not in block
    assert "OTELCOL_LOG_FILES" not in block
    assert not re.search(r'user\s*=\s*"0"', block)
    assert 'resource "docker_volume" "collector"' not in text
    assert "docker_volume.collector" not in text
    assert re.search(r'capabilities\s*\{\s*drop\s*=\s*\["ALL"\]\s*\}', block)
    assert re.search(r"no-new-privileges:true", block)
    assert re.search(r'tmpfs\s*=\s*\{\s*"/tmp"', block)
    assert not re.search(r"read_only\s*=\s*true", block)


@pytest.mark.parametrize("service", ["api", "worker"])
def test_compose_log_exporter_defaults_to_none(service: str) -> None:
    """Логи экспортируются тем же переключателем, что трейсы: по умолчанию
    выключены, с профилем observability — otlp (см. комментарий у api)."""
    assert _compose_env_defaults(service)["OTEL_LOGS_EXPORTER"] == "none"


@pytest.mark.parametrize("service", ["api", "worker"])
def test_compose_otlp_timeout_is_four_seconds(service: str) -> None:
    """Дефолт таймаута OTLP — 4: Python SDK читает переменную в СЕКУНДАХ,
    вопреки спеке OTel (мс)."""
    assert _compose_env_defaults(service)["OTEL_EXPORTER_OTLP_TIMEOUT"] == "4"


@pytest.mark.parametrize(
    ("service", "instance"),
    [("api", "dmc268-api"), ("worker", "dmc268-worker")],
)
def test_compose_resource_attributes_pin_a_stable_instance_id(
    service: str, instance: str
) -> None:
    """service.instance.id в resource — стабильное имя сервиса, не UUID:
    случайный идентификатор на процесс растил бы в Prometheus серии
    exported_instance (ресурс конвертируется в лейблы)."""
    attributes = _compose_env_defaults(service)["OTEL_RESOURCE_ATTRIBUTES"].split(",")
    assert f"service.instance.id={instance}" in attributes


def test_infra_telemetry_env_sets_seconds_timeout_and_log_exporter() -> None:
    """telemetry_env: таймаут в секундах (SDK Python читает переменную в
    СЕКУНДАХ, вопреки спеке OTel — мс) и включённый экспортёр логов."""
    text = LOCALS_TF.read_text(encoding="utf-8")
    assert '"OTEL_EXPORTER_OTLP_TIMEOUT=4"' in text
    assert "OTEL_EXPORTER_OTLP_TIMEOUT=4000" not in text
    assert '"OTEL_LOGS_EXPORTER=otlp"' in text


def test_infra_telemetry_env_caps_batch_queues() -> None:
    """telemetry_env ставит потолки очередей батч-процессоров спанов (BSP) и
    логов (BLRP): shutdown_tracing закрывает три провайдера телеметрии
    последовательно, и с очередью по умолчанию остановка при лежащем
    коллекторе не влезала в стоп-окно api (замечание ревью PR #130)."""
    text = LOCALS_TF.read_text(encoding="utf-8")
    assert '"OTEL_BSP_MAX_QUEUE_SIZE=512"' in text
    assert '"OTEL_BLRP_MAX_QUEUE_SIZE=512"' in text


@pytest.mark.parametrize("service", ["api", "worker", "migrate"])
def test_compose_caps_batch_queues_by_default(service: str) -> None:
    """Дефолт потолков очередей в compose — 512, как в telemetry_env infra."""
    assert _compose_env_defaults(service)["OTEL_BSP_MAX_QUEUE_SIZE"] == "512"
    assert _compose_env_defaults(service)["OTEL_BLRP_MAX_QUEUE_SIZE"] == "512"


@pytest.mark.parametrize(
    ("container", "instance"),
    [("api", "dmc268-api"), ("worker", "dmc268-worker")],
)
def test_infra_resource_attributes_pin_a_stable_instance_id(
    container: str, instance: str
) -> None:
    """service.instance.id — стабильное имя контейнера, не UUID (смысл —
    в тесте compose выше). У миграций своего контейнера больше нет:
    идентификатор dmc268-migrations проверяет тест пароля роли выше
    (выход migrate_env)."""
    block = _tf_container_block(container)
    match = re.search(
        r"OTEL_RESOURCE_ATTRIBUTES=[^\n]+service\.instance\.id=(?P<instance>[\w-]+)",
        block,
    )
    assert match is not None, f"в env {container} нет service.instance.id"
    assert match.group("instance") == instance
    # Источник истины для идентификатора — имя контейнера.
    name = re.search(r'name\s*=\s*"(?P<name>[^"]+)"', block)
    assert name is not None and name.group("name") == instance


# --- дашборд Grafana (задача 8) ------------------------------------------------------


def test_dashboard_provider_points_to_the_mounted_directory() -> None:
    """Провайдер file-типа сканирует каталог, который смонтирован в контейнер.

    compose монтирует provisioning целиком в /etc/grafana/provisioning, infra
    заливает файлы по тем же путям: JSON лежит рядом с провайдером, отдельного
    тома не нужно.
    """
    provider: dict[str, Any] = yaml.safe_load(PROVIDER_CONFIG.read_text(encoding="utf-8"))
    assert provider["apiVersion"] == 1
    (provider_block,) = provider["providers"]
    assert provider_block["type"] == "file"
    assert provider_block["options"]["path"] == "/etc/grafana/provisioning/dashboards"
    # Каталог, смонтированный по этому пути, содержит сам дашборд.
    assert DASHBOARD_JSON.is_file()


def load_dashboard() -> dict[str, Any]:
    dashboard: dict[str, Any] = json.loads(DASHBOARD_JSON.read_text(encoding="utf-8"))
    return dashboard


def test_dashboard_uid_and_title_are_fixed() -> None:
    """Имя и uid дашборда зафиксированы: на них опирается провижининг."""
    dashboard = load_dashboard()
    assert dashboard["uid"] == "dmc268-overview"
    assert DASHBOARD_JSON.stem == "dmc268-overview"
    assert dashboard["title"]


def test_every_panel_and_target_uses_the_prometheus_datasource() -> None:
    """Панели и их запросы привязаны к uid datasource из datasources.yaml."""
    for panel in load_dashboard()["panels"]:
        assert panel["datasource"]["uid"] == "prometheus", panel["title"]
        for target in panel["targets"]:
            assert target["datasource"]["uid"] == "prometheus", panel["title"]


def test_dashboard_queries_cover_the_expected_signals() -> None:
    """Санитарный минимум по выражениям: сигналы из спеки присутствуют.

    Имена — как их отдаёт exporter-порт коллектора: точки в `_`, суффикс
    единицы измерения (`review_llm_call_duration` с unit="s" становится
    `review_llm_call_duration_seconds`), лейбл job = service.name.
    """
    expressions = " ".join(
        target["expr"] for panel in load_dashboard()["panels"] for target in panel["targets"]
    )
    expected = (
        "http_server_request_duration_seconds_count",
        'http_response_status_code=~"5.."',
        'http_route!="/api/v1/events"',
        "review_llm_call_duration_seconds",
        "review_stage_duration_seconds",
        "review_runs_total",
        'rabbitmq_queue_messages{queue="review_jobs.qq"}',
        'rabbitmq_queue_consumers{queue="review_jobs.qq"}',
        'rabbitmq_queue_messages_unacked{queue="review_jobs.qq"}',
        "db_client_connection_count",
        "db_client_connection_max",
        "postgresql_backends",
        "postgresql_connection_max",
        "node_cpu_seconds_total",
        "node_memory_MemAvailable_bytes",
        "node_filesystem_avail_bytes",
        "container_cpu_usage_nanoseconds_total",
        "container_memory_usage_total_bytes",
    )
    missing = [signal for signal in expected if signal not in expressions]
    assert not missing, f"нет сигналов в выражениях дашборда: {missing}"
