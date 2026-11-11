"""Контракт `GET /api/v1/status`: открыт гостю, форма ответа, только статусы. Без БД и сети."""

import re
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.dependencies import get_service_status_monitor
from app.api.factory import create_app
from app.application.use_cases.service_status import ServiceStatusMonitor
from app.config import Settings
from app.domain.service_status import FiringAlert

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
    public_origin="https://review.example",
)

CHECKED_AT = datetime(2026, 11, 2, 12, 0, 5, tzinfo=UTC)


class _Source:
    def __init__(self, alerts: tuple[FiringAlert, ...] | None) -> None:
        self.alerts = alerts
        self.calls = 0

    def firing_alerts(self) -> tuple[FiringAlert, ...] | None:
        self.calls += 1
        return self.alerts


def _client(source: _Source) -> TestClient:
    app = create_app(SETTINGS)
    monitor = ServiceStatusMonitor(source, ttl_seconds=15, monotonic=lambda: 0.0, now=lambda: CHECKED_AT)
    app.dependency_overrides[get_service_status_monitor] = lambda: monitor
    return TestClient(app)


def test_a_guest_without_a_session_gets_the_status() -> None:
    source = _Source(
        (
            FiringAlert(
                "ContainerDown",
                {"alertname": "ContainerDown", "container_name": "dmc268-worker", "instance": "collector-docker:8889"},
            ),
            FiringAlert("ContainerRestarting", {"container_name": "dmc268-rabbitmq"}),
        )
    )
    response = _client(source).get("/api/v1/status")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["x-trace-id"])
    # Один экземпляр воркера не запущен — degraded (#99): задачи забирают остальные.
    assert response.json() == {
        "status": "degraded",
        "components": [
            {"id": "api", "status": "operational"},
            {"id": "worker", "status": "degraded"},
            {"id": "queue", "status": "degraded"},
        ],
        "checked_at": "2026-11-02T12:00:05Z",
    }
    # Наружу — только статусы: ни имён алертов, ни меток, ни адресов.
    for leaked in ("ContainerDown", "dmc268", "collector", "instance"):
        assert leaked not in response.text


def test_an_unreachable_source_is_200_unknown() -> None:
    response = _client(_Source(None)).get("/api/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "unknown"
    assert [c["status"] for c in body["components"]] == ["unknown"] * 3


def test_a_foreign_origin_and_a_bogus_cookie_do_not_matter() -> None:
    """GET не сверяет Origin, а сессию ручка не читает: битая cookie — тоже гость."""
    client = _client(_Source(()))
    client.cookies.set("session", "not-a-session")
    response = client.get("/api/v1/status", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    assert response.json()["status"] == "operational"


def test_repeated_requests_reuse_the_cached_evaluation() -> None:
    source = _Source(())
    client = _client(source)
    for _ in range(5):
        assert client.get("/api/v1/status").status_code == 200
    assert source.calls == 1


def test_without_prometheus_url_the_real_container_answers_unknown() -> None:
    """Настоящий контейнер без PROMETHEUS_URL: запросов нет, статус unknown, не 5xx."""
    response = TestClient(create_app(SETTINGS)).get("/api/v1/status")
    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


def test_the_contract_declares_enums_and_no_session() -> None:
    schema = create_app(SETTINGS).openapi()
    operation = schema["paths"]["/api/v1/status"]["get"]
    assert operation["operationId"] == "getServiceStatus"
    assert operation["security"] == []
    assert operation["tags"] == ["status"]
    schemas = schema["components"]["schemas"]
    assert schemas["ServiceStatus"]["enum"] == ["operational", "degraded", "outage", "unknown"]
    assert schemas["ServiceComponent"]["enum"] == ["api", "worker", "queue"]
    response = schemas["ServiceStatusResponse"]
    assert set(response["required"]) == {"status", "components", "checked_at"}
    assert set(response["properties"]) == {"status", "components", "checked_at"}
