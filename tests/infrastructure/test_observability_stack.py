"""Структура стека наблюдаемости без Docker: compose и infra описывают одно.

Правки конфигов не поднимают стек, а рассинхрон compose и infra/ обнаружился
бы только на деплое. Тесты держат структуру: образы стека зафиксированы
дайджестами одинаково в обоих местах, отдельного экспортёра Postgres больше
нет (метрики базы снимает receiver postgresql коллектора, решение #89),
сервер и контейнеры видны (node-exporter, cAdvisor), у Prometheus есть
потолок по размеру, имена compose не сталкиваются с infra на общем хосте.
Валидацию конфига самим коллектором делает класс
TestCollectorConfigValidation в test_collector_redaction.py.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO / "docker-compose.yml"
MAIN_TF = REPO / "infra" / "main.tf"
VARIABLES_TF = REPO / "infra" / "variables.tf"
PROMETHEUS_CONFIG = REPO / "infra" / "observability" / "prometheus.yml"
README = REPO / "infra" / "README.md"
DASHBOARDS_DIR = REPO / "infra" / "observability" / "grafana" / "provisioning" / "dashboards"
PROVIDER_CONFIG = DASHBOARDS_DIR / "dashboards.yaml"
DASHBOARD_JSON = DASHBOARDS_DIR / "dmc268-overview.json"

# Сервисы стека наблюдаемости и их переменные-образы в locals main.tf:
# пар «сервис compose → local» обязан совпадать дайджестами в обоих файлах.
STACK_SERVICES = {
    "collector": "otelcol_image",
    "loki": "loki_image",
    "tempo": "tempo_image",
    "prometheus": "prometheus_image",
    "grafana": "grafana_image",
    "node-exporter": "node_exporter_image",
    "cadvisor": "cadvisor_image",
}

# имя@sha256:… — дайджест манифест-листа (multi-arch), а не платформенный.
_DIGEST = re.compile(r"^(?P<name>[^@\s]+)@sha256:(?P<digest>[0-9a-f]{64})$")


def load_compose() -> dict[str, Any]:
    compose: dict[str, Any] = yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))
    return compose


def load_prometheus() -> dict[str, Any]:
    config: dict[str, Any] = yaml.safe_load(PROMETHEUS_CONFIG.read_text(encoding="utf-8"))
    return config


def parse_tf_locals() -> dict[str, str]:
    """Переменные locals main.tf: имя → значение-строка."""
    locals: dict[str, str] = {}
    for name, value in re.findall(r'^\s*(\w+)\s+=\s+"([^"]*)"', MAIN_TF.read_text(encoding="utf-8"), re.MULTILINE):
        locals[name] = value
    return locals


# --- postgres-exporter удалён целиком -----------------------------------------------


@pytest.mark.parametrize(
    ("path", "needle"),
    [
        (COMPOSE_FILE, "postgres-exporter"),
        (MAIN_TF, "postgres_exporter"),
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


# --- node-exporter и cAdvisor в обоих окружениях ------------------------------------


@pytest.mark.parametrize("service", ["node-exporter", "cadvisor"])
def test_host_and_container_exporters_in_compose(service: str) -> None:
    """Сервер и контейнеры видны: сервисы в профиле observability, без портов наружу."""
    service_config = load_compose()["services"][service]
    assert "observability" in service_config["profiles"]


@pytest.mark.parametrize("container", ["node_exporter", "cadvisor"])
def test_host_and_container_exporters_in_infra(container: str) -> None:
    """То же в infra: контейнер, образ из locals и потолок памяти из переменной."""
    text = MAIN_TF.read_text(encoding="utf-8")
    assert f'resource "docker_image" "{container}"' in text
    assert f'resource "docker_container" "{container}"' in text
    assert f"var.observability_memory_mb.{container}" in text


def test_prometheus_scrapes_new_exporters_by_service_hosts() -> None:
    """Jobs node-exporter и cadvisor по хостнеймам сервисов и их портам."""
    jobs = {job["job_name"]: job for job in load_prometheus()["scrape_configs"]}
    assert jobs["node-exporter"]["static_configs"][0]["targets"] == ["node-exporter:9100"]
    assert jobs["cadvisor"]["static_configs"][0]["targets"] == ["cadvisor:8080"]


def test_rabbitmq_job_scrapes_per_object_metrics() -> None:
    """RabbitMQ скреится с per-object эндпоинта: агрегированный /metrics
    теряет лейбл queue, по которому панель очереди фильтрует review_jobs."""
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
    """Все семь образов стека — имя@sha256:…, одинаковые в compose и main.tf.

    Тег плавает, дайджест манифест-листа нет: рассинхрон между окружениями —
    ошибка, поэтому сравниваются именно дайджесты, а не полные ссылки.
    """
    assert _compose_digests() == _infra_digests()


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


def test_grafana_volume_has_no_fixed_name() -> None:
    """Том grafana_data получает префикс compose-проекта: два проекта с одним
    фиксированным именем тома на общем хосте не поднимаются вместе."""
    # grafana_data без отображения yaml разбирает в None — ключей у него нет.
    assert not (load_compose()["volumes"]["grafana_data"] or {}).get("name")


# --- лимиты и preload: одно и то же в compose и infra -------------------------------


@pytest.mark.parametrize("file", [COMPOSE_FILE, MAIN_TF])
def test_prometheus_is_capped_by_size(file: Path) -> None:
    """Потолок по размеру рядом с потолком по времени: всплеск кардинальности
    не должен забить диск раньше срока хранения."""
    assert "--storage.tsdb.retention.size=8GB" in file.read_text(encoding="utf-8")


@pytest.mark.parametrize("file", [COMPOSE_FILE, MAIN_TF])
def test_postgres_preloads_pg_stat_statements(file: Path) -> None:
    """pg_stat_statements загружается сервером в обоих окружениях: тяжёлые
    запросы снимает receiver postgresql (расширение создаёт миграция 0013)."""
    assert "shared_preload_libraries=pg_stat_statements" in file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("path", "variables"),
    [
        (COMPOSE_FILE, ("POSTGRESQL_USERNAME", "POSTGRESQL_PASSWORD", "POSTGRESQL_ENDPOINT", "POSTGRESQL_DATABASE")),
        (MAIN_TF, ("POSTGRESQL_USERNAME", "POSTGRESQL_PASSWORD", "POSTGRESQL_ENDPOINT", "POSTGRESQL_DATABASE")),
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


def _tf_container_block(name: str) -> str:
    """Текст блока docker_container в main.tf по имени ресурса.

    Вложенные блоки сдвинуты отступом, поэтому конец ресурса — первая
    закрывающая скобка в начале строки после заголовка.
    """
    match = re.search(
        rf'resource "docker_container" "{name}" \{{.*?\n\}}',
        MAIN_TF.read_text(encoding="utf-8"),
        re.DOTALL,
    )
    assert match is not None, f"нет docker_container.{name} в main.tf"
    return match.group(0)


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
    text = MAIN_TF.read_text(encoding="utf-8")
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
    text = MAIN_TF.read_text(encoding="utf-8")
    assert '"OTEL_EXPORTER_OTLP_TIMEOUT=4"' in text
    assert "OTEL_EXPORTER_OTLP_TIMEOUT=4000" not in text
    assert '"OTEL_LOGS_EXPORTER=otlp"' in text


@pytest.mark.parametrize(
    ("container", "instance"),
    [("migrate", "dmc268-migrate"), ("api", "dmc268-api"), ("worker", "dmc268-worker")],
)
def test_infra_resource_attributes_pin_a_stable_instance_id(
    container: str, instance: str
) -> None:
    """service.instance.id — стабильное имя контейнера, не UUID (смысл —
    в тесте compose выше)."""
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
        'rabbitmq_queue_messages{queue="review_jobs"}',
        'rabbitmq_queue_head_message_timestamp{queue="review_jobs"}',
        "db_client_connection_count",
        "db_client_connection_max",
        "postgresql_backends",
        "postgresql_connection_max",
        "node_cpu_seconds_total",
        "node_memory_MemAvailable_bytes",
        "node_filesystem_avail_bytes",
        "container_cpu_usage_seconds_total",
        "container_memory_usage_bytes",
    )
    missing = [signal for signal in expected if signal not in expressions]
    assert not missing, f"нет сигналов в выражениях дашборда: {missing}"
