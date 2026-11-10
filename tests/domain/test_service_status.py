"""Статус сервиса по сработавшим алертам: таблица, слепота, порядок тяжести.

Чистые функции — без сети. Последние тесты сверяют таблицу с `infra/observability/`:
переименованный алерт, метка или job иначе молча сделали бы компонент вечно `operational`.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml

from app.domain.enums import ServiceComponent, ServiceStatus
from app.domain.service_status import (
    ALERT_RULES,
    ComponentStatus,
    FiringAlert,
    ServiceStatusReport,
    evaluate_service_status,
    worst_status,
)

from .conftest import NOW

API, WORKER, QUEUE = ServiceComponent.API, ServiceComponent.WORKER, ServiceComponent.QUEUE
OPERATIONAL = ServiceStatus.OPERATIONAL
DEGRADED = ServiceStatus.DEGRADED
OUTAGE = ServiceStatus.OUTAGE
UNKNOWN = ServiceStatus.UNKNOWN

OBSERVABILITY = Path(__file__).resolve().parents[2] / "infra" / "observability"


def _statuses(*alerts: FiringAlert) -> dict[ServiceComponent, ServiceStatus]:
    report = evaluate_service_status(list(alerts), NOW)
    return {c.component: c.status for c in report.components}


# Каждый алерт таблицы — компоненты и статус, остальные компоненты не задеты.
SIGNALS = [
    ("ContainerDown", {"container_name": "dmc268-api"}, {API}, OUTAGE),
    ("ServiceProbeFailed", {"service": "api", "instance": "http://dmc268-api:8000/health"}, {API}, OUTAGE),
    ("Api5xxSpike", {"service": "api"}, {API}, DEGRADED),
    ("ContainerRestarting", {"container_name": "dmc268-api"}, {API}, DEGRADED),
    ("ContainerDown", {"container_name": "dmc268-worker"}, {WORKER}, OUTAGE),
    ("WorkerNotConsuming", {"service": "worker", "queue": "review_jobs.qq"}, {WORKER}, OUTAGE),
    ("ContainerRestarting", {"container_name": "dmc268-worker"}, {WORKER}, DEGRADED),
    ("ReviewRunsFailing", {"service": "worker"}, {WORKER}, DEGRADED),
    ("WorkerStalled", {"service": "worker", "queue": "review_jobs.qq"}, {WORKER}, DEGRADED),
    ("ReviewJobStuck", {"service": "worker", "queue": "review_jobs.qq"}, {WORKER}, DEGRADED),
    ("ContainerDown", {"container_name": "dmc268-rabbitmq"}, {QUEUE}, OUTAGE),
    ("ServiceProbeFailed", {"service": "rabbitmq", "instance": "dmc268-rabbitmq:5672"}, {QUEUE}, OUTAGE),
    ("ContainerRestarting", {"container_name": "dmc268-rabbitmq"}, {QUEUE}, DEGRADED),
    # База: /health API её не проверяет, но без неё API отвечает 500, а воркер не работает.
    ("ContainerDown", {"container_name": "dmc268-postgres"}, {API, WORKER}, OUTAGE),
    ("ServiceProbeFailed", {"service": "postgres", "instance": "dmc268-postgres:5432"}, {API, WORKER}, OUTAGE),
]


@pytest.mark.parametrize(("name", "labels", "components", "status"), SIGNALS)
def test_each_alert_sets_its_components(
    name: str, labels: dict[str, str], components: set[ServiceComponent], status: ServiceStatus
) -> None:
    expected = {c: status if c in components else OPERATIONAL for c in ServiceComponent}
    assert _statuses(FiringAlert(name, labels)) == expected


def test_the_signal_list_covers_the_whole_table_except_blindness() -> None:
    """Сверка по меткам строки, а не только по имени: два `ContainerDown` у api —
    разные строки (контейнер API и контейнер базы)."""
    rules = [rule for rule in ALERT_RULES if rule.status is not UNKNOWN]
    assert len(SIGNALS) == len(rules)
    for rule in rules:
        assert any(
            name == rule.alert
            and status is rule.status
            and components == set(rule.components)
            and rule.labels.items() <= labels.items()
            for name, labels, components, status in SIGNALS
        ), rule


BLIND = [
    ({"alertname": "ScrapeTargetDown", "job": "docker-stats"}, {API, WORKER, QUEUE}),
    ({"alertname": "ContainerMetricsMissing"}, {API, WORKER, QUEUE}),
    ({"alertname": "ScrapeTargetDown", "job": "blackbox-services"}, {API, WORKER, QUEUE}),
    ({"alertname": "ScrapeTargetDown", "job": "collector"}, {API, WORKER}),
    ({"alertname": "ScrapeTargetDown", "job": "rabbitmq"}, {WORKER}),
]


@pytest.mark.parametrize(("labels", "blind"), BLIND)
def test_a_blind_source_makes_its_components_unknown(labels: dict[str, str], blind: set[ServiceComponent]) -> None:
    alert = FiringAlert(labels["alertname"], {**labels, "service": "observability"})
    assert _statuses(alert) == {c: UNKNOWN if c in blind else OPERATIONAL for c in ServiceComponent}


def test_the_blind_list_covers_the_table() -> None:
    blind_rules = [rule for rule in ALERT_RULES if rule.status is UNKNOWN]
    assert len(blind_rules) == len(BLIND)


@pytest.mark.parametrize(
    "alert",
    [
        FiringAlert("ScrapeTargetDown", {"job": "loki"}),
        FiringAlert("ScrapeTargetDown", {"job": "node-exporter"}),
        FiringAlert("ContainerDown", {"container_name": "dmc268-postgres-replica"}),
        FiringAlert("ContainerDown", {"container_name": "dmc268-ui"}),
        FiringAlert("ContainerRestarting", {"container_name": "dmc268-grafana"}),
        FiringAlert("ContainerMemoryNearLimit", {"container_name": "dmc268-blackbox", "service": "blackbox"}),
        FiringAlert("ServiceProbeFailed", {"service": "postgres-replica"}),
        FiringAlert("DiskSpaceCritical", {"mountpoint": "/"}),
        FiringAlert("LlmBudgetExceeded", {"service": "llm"}),
        FiringAlert("PublicEndpointDown", {"service": "ui"}),
        FiringAlert("BackupStale", {"service": "postgres-backup"}),
        # У DLQ нет TTL: одно сообщение держало бы degraded до ручного разбора, а падающие
        # прогоны и так ловит ReviewRunsFailing.
        FiringAlert("ReviewDlqNotEmpty", {"service": "worker", "queue": "review_jobs.dlq"}),
        # Dead man's switch горит всегда (#334): он о цепочке алертов, не о сервисе.
        FiringAlert("Watchdog", {"severity": "none"}),
        # Сломанная доставка в Telegram — о цепочке алертов, не о сервисе.
        FiringAlert("AlertDeliveryFailing", {"severity": "critical", "service": "alertmanager"}),
        # Метки без имени нужного правила ничего не значат.
        FiringAlert("SomethingElse", {"container_name": "dmc268-api"}),
        # Имя правила без нужной метки — тоже.
        FiringAlert("ContainerDown", {}),
    ],
)
def test_unrelated_alerts_do_not_touch_the_page(alert: FiringAlert) -> None:
    assert _statuses(alert) == dict.fromkeys(ServiceComponent, OPERATIONAL)


def test_no_alerts_is_operational() -> None:
    report = evaluate_service_status([], NOW)
    assert report == ServiceStatusReport(
        OPERATIONAL,
        (
            ComponentStatus(API, OPERATIONAL),
            ComponentStatus(WORKER, OPERATIONAL),
            ComponentStatus(QUEUE, OPERATIONAL),
        ),
        NOW,
    )


def test_unknown_alerts_make_everything_unknown() -> None:
    report = evaluate_service_status(None, NOW)
    assert report.status is UNKNOWN
    assert [c.status for c in report.components] == [UNKNOWN, UNKNOWN, UNKNOWN]
    assert report.checked_at == NOW


def test_components_always_come_in_fixed_order() -> None:
    report = evaluate_service_status([FiringAlert("ContainerDown", {"container_name": "dmc268-rabbitmq"})], NOW)
    assert [c.component for c in report.components] == [API, WORKER, QUEUE]


@pytest.mark.parametrize(
    ("statuses", "worst"),
    [
        ([], OPERATIONAL),
        ([OPERATIONAL, OPERATIONAL], OPERATIONAL),
        ([OPERATIONAL, UNKNOWN], UNKNOWN),
        ([UNKNOWN, DEGRADED], DEGRADED),
        ([DEGRADED, OUTAGE, UNKNOWN], OUTAGE),
        ([OUTAGE, OPERATIONAL], OUTAGE),
    ],
)
def test_worst_status_order(statuses: list[ServiceStatus], worst: ServiceStatus) -> None:
    assert worst_status(statuses) is worst


def test_outage_beats_blindness_and_degradation() -> None:
    statuses = _statuses(
        FiringAlert("ScrapeTargetDown", {"job": "docker-stats"}),
        FiringAlert("Api5xxSpike"),
        FiringAlert("ServiceProbeFailed", {"service": "api"}),
    )
    assert statuses[API] is OUTAGE


def test_a_confirmed_degradation_is_not_hidden_by_blindness() -> None:
    statuses = _statuses(FiringAlert("ScrapeTargetDown", {"job": "rabbitmq"}), FiringAlert("ReviewRunsFailing"))
    assert statuses[WORKER] is DEGRADED


def test_overall_is_the_worst_component() -> None:
    report = evaluate_service_status(
        [
            FiringAlert("ScrapeTargetDown", {"job": "rabbitmq"}),
            FiringAlert("ContainerRestarting", {"container_name": "dmc268-rabbitmq"}),
        ],
        NOW,
    )
    assert [c.status for c in report.components] == [OPERATIONAL, UNKNOWN, DEGRADED]
    assert report.status is DEGRADED

    blind_only = evaluate_service_status([FiringAlert("ScrapeTargetDown", {"job": "rabbitmq"})], NOW)
    assert blind_only.status is UNKNOWN

    down = evaluate_service_status(
        [FiringAlert("WorkerNotConsuming"), FiringAlert("ContainerMetricsMissing")],
        NOW,
    )
    assert [c.status for c in down.components] == [UNKNOWN, OUTAGE, UNKNOWN]
    assert down.status is OUTAGE


# --- сверка с infra/observability ---------------------------------------------


def _alert_rules() -> dict[str, dict[str, Any]]:
    config = yaml.safe_load((OBSERVABILITY / "alerts.yml").read_text(encoding="utf-8"))
    return {rule["alert"]: rule for group in config["groups"] for rule in group["rules"] if "alert" in rule}


def _prometheus() -> dict[str, Any]:
    config: dict[str, Any] = yaml.safe_load((OBSERVABILITY / "prometheus.yml").read_text(encoding="utf-8"))
    return config


def test_every_table_alert_exists_in_the_rules() -> None:
    missing = {rule.alert for rule in ALERT_RULES} - set(_alert_rules())
    assert not missing, f"алертов нет в alerts.yml: {missing}"


def test_matched_labels_come_from_the_series_not_from_the_rule() -> None:
    """Метка правила перекрыла бы метку ряда: `service: observability` у ScrapeTargetDown
    затирает `service` цели, поэтому таблица сопоставляет его по `job`, а не по `service`."""
    rules = _alert_rules()
    for rule in ALERT_RULES:
        overridden = set(rule.labels) & set(rules[rule.alert].get("labels", {}))
        assert not overridden, f"{rule.alert}: метки {overridden} задаёт само правило"


def test_container_names_are_watched_by_container_down() -> None:
    expr = _alert_rules()["ContainerDown"]["expr"]
    for rule in ALERT_RULES:
        name = rule.labels.get("container_name")
        if name is not None:
            assert f'container_name="{name}"' in expr, name


def test_probe_services_and_scrape_jobs_exist() -> None:
    jobs = {job["job_name"]: job for job in _prometheus()["scrape_configs"]}
    probes = {target["labels"]["service"] for target in jobs["blackbox-services"]["static_configs"]}
    for rule in ALERT_RULES:
        if "service" in rule.labels:
            assert rule.labels["service"] in probes, rule
        if "job" in rule.labels:
            assert rule.labels["job"] in jobs, rule
