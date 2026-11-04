"""Адаптер алертов Prometheus над `httpx.MockTransport`: запрос, разбор и каждый сбой — `None`."""

import json
import logging
from collections.abc import Callable

import httpx
import pytest

from app.domain.service_status import FiringAlert
from app.infrastructure.monitoring.prometheus import (
    FIRING_ALERTS_QUERY,
    QUERY_PATH,
    PrometheusAlertSource,
)

BASE_URL = "http://dmc268-prometheus:9090"


def _source(handler: Callable[[httpx.Request], httpx.Response]) -> PrometheusAlertSource:
    return PrometheusAlertSource(httpx.Client(base_url=BASE_URL, transport=httpx.MockTransport(handler)))


def _vector(*metrics: dict[str, str]) -> dict[str, object]:
    return {
        "status": "success",
        "data": {"resultType": "vector", "result": [{"metric": m, "value": [1762084800.0, "1"]} for m in metrics]},
    }


def test_firing_alerts_are_read_with_their_labels() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=_vector(
                {
                    "__name__": "ALERTS",
                    "alertname": "ContainerDown",
                    "alertstate": "firing",
                    "container_name": "dmc268-worker",
                    "severity": "critical",
                },
                {"__name__": "ALERTS", "alertname": "Api5xxSpike", "alertstate": "firing", "service": "api"},
            ),
        )

    alerts = _source(handler).firing_alerts()

    assert [request.url.path for request in seen] == [QUERY_PATH]
    assert seen[0].url.params["query"] == FIRING_ALERTS_QUERY
    assert alerts is not None
    assert [(a.name, a.labels.get("container_name")) for a in alerts] == [
        ("ContainerDown", "dmc268-worker"),
        ("Api5xxSpike", None),
    ]
    assert isinstance(alerts[0], FiringAlert)


def test_no_firing_alerts_is_an_empty_tuple() -> None:
    assert _source(lambda _: httpx.Response(200, json=_vector())).firing_alerts() == ()


def test_without_an_address_nothing_is_requested() -> None:
    assert PrometheusAlertSource(None).firing_alerts() is None


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (_timeout, "ReadTimeout"),
        (_refused, "ConnectError"),
        (lambda _: httpx.Response(500, text="internal"), "HTTP 500"),
        (lambda _: httpx.Response(503, json={"status": "error", "error": "unavailable"}), "HTTP 503"),
        (lambda _: httpx.Response(200, text="<html>not json</html>"), "не по формату"),
        (lambda _: httpx.Response(200, json={"status": "error", "errorType": "bad_data"}), "не по формату"),
        (
            lambda _: httpx.Response(200, json={"status": "success", "data": {"resultType": "matrix", "result": []}}),
            "не по формату",
        ),
        (
            lambda _: httpx.Response(200, content=json.dumps(_vector({"alertname": 1})).encode()),  # type: ignore[dict-item]
            "не по формату",
        ),
    ],
)
def test_any_failure_is_none_with_a_warning(
    handler: Callable[[httpx.Request], httpx.Response], reason: str, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.infrastructure.monitoring.prometheus"):
        assert _source(handler).firing_alerts() is None
    assert [record.levelno for record in caplog.records] == [logging.WARNING]
    assert reason in caplog.text
    # Тело ответа в лог не попадает.
    assert "internal" not in caplog.text and "unavailable" not in caplog.text
