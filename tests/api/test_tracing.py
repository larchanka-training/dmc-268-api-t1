"""Трассировка HTTP API: `X-Trace-ID` — trace id спана запроса. Без базы, брокера и сети.

Спаны пишет в память провайдер из `tests/conftest.py` (фикстура `spans`).
"""

import ast
import asyncio
import os
import re
import subprocess
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import propagate
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, format_trace_id
from starlette.types import Message

from app.api.dependencies import get_run_reader, get_sleep, get_unit_of_work_factory
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import ReviewRun
from app.domain.enums import ReviewRunStatus as S
from app.domain.enums import TriggerSource

from ..fakes.auth import sign_in_with_session
from ..fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)
ORIGIN = "http://localhost:5173"
FOREIGN_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
FOREIGN_TRACEPARENT = f"00-{FOREIGN_TRACE_ID}-00f067aa0ba902b7-01"
RUN_ID = UUID("00000000-0000-0000-0000-0000000000aa")


def _app() -> FastAPI:
    app = create_app(SETTINGS)
    uow = FakeUnitOfWork()
    app.dependency_overrides[get_unit_of_work_factory] = lambda: lambda: uow

    @app.get("/api/v1/_boom")
    def boom() -> None:
        raise RuntimeError("сбой")

    return app


def _server_span(spans: InMemorySpanExporter) -> ReadableSpan:
    (span,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER]
    return span


def _trace_id(span: ReadableSpan) -> str:
    assert span.context is not None
    return format_trace_id(span.context.trace_id)


@pytest.mark.parametrize(
    ("path", "status"),
    [("/api/v1/no-such-route", 404), ("/api/v1/_boom", 500)],
)
def test_trace_id_of_an_error_is_the_trace_id_of_the_request_span(
    spans: InMemorySpanExporter, path: str, status: int
) -> None:
    # `500` отдаёт обработчик снаружи всех middleware — trace id и там тот же.
    client = TestClient(_app(), base_url=ORIGIN, raise_server_exceptions=False)

    response = client.get(path)

    assert response.status_code == status
    span_trace_id = _trace_id(_server_span(spans))
    assert re.fullmatch(r"[0-9a-f]{32}", span_trace_id)
    assert response.headers["X-Trace-ID"] == span_trace_id
    assert response.json()["trace_id"] == span_trace_id


def test_a_successful_response_carries_the_trace_id_of_its_span(
    spans: InMemorySpanExporter,
) -> None:
    # `/health` теперь вне трейсинга — обычный путь обязан нести trace id спана.
    response = TestClient(_app()).get("/")

    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == _trace_id(_server_span(spans))


def test_health_check_creates_no_span(spans: InMemorySpanExporter) -> None:
    """Health-check опрашивают Docker и деплой: спанов на нём нет, трейс не шумит.

    Другой путь спан создаёт — трейсинг выключен только для проверок живости.
    """
    client = TestClient(_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert spans.get_finished_spans() == ()
    assert client.get("/").status_code == 200
    assert [s.kind for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER]


def test_an_incoming_traceparent_is_ignored(spans: InMemorySpanExporter) -> None:
    response = TestClient(_app()).get(
        "/", headers={"traceparent": FOREIGN_TRACEPARENT, "X-Trace-ID": "f" * 32}
    )

    server = _server_span(spans)
    assert server.parent is None  # запрос начал новый трейс
    assert _trace_id(server) != FOREIGN_TRACE_ID
    assert response.headers["X-Trace-ID"] == _trace_id(server)


def test_the_auth_callback_query_is_hidden_in_the_request_span(
    spans: InMemorySpanExporter,
) -> None:
    """`code` и `state` callback'а OAuth не попадают в атрибуты спана, как и в access-лог."""
    client = TestClient(_app(), follow_redirects=False)

    response = client.get(
        "/api/v1/auth/github/callback",
        params={"code": "code-secret-1", "state": "state-secret-1"},
    )

    server = _server_span(spans)
    assert server.attributes is not None
    assert server.attributes["url.query"] == "<скрыто>"
    values = " ".join(str(value) for value in server.attributes.values())
    assert "code-secret-1" not in values and "state-secret-1" not in values
    assert response.headers["X-Trace-ID"] == _trace_id(server)


def test_the_query_outside_auth_stays_in_the_request_span(
    spans: InMemorySpanExporter,
) -> None:
    TestClient(_app()).get("/", params={"page": "2"})

    attributes = _server_span(spans).attributes
    assert attributes is not None
    assert attributes["url.query"] == "page=2"


# Глобальный провайдер ставится один раз на процесс, и в тестах это провайдер из
# `tests/conftest.py`; `OTEL_SDK_DISABLED` SDK читает при создании провайдера. Поэтому
# настоящий выключенный режим — в отдельном процессе: глобальное состояние этого не
# трогается.
_DISABLED_SCRIPT = """
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from app.api.factory import create_app
from app.config import Settings

app = create_app(
    Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="test-secret",
    )
)
provider = trace.get_tracer_provider()
assert isinstance(provider, TracerProvider)
exporter = InMemorySpanExporter()
provider.add_span_processor(SimpleSpanProcessor(exporter))
client = TestClient(app)
# `/` — трассируемый путь: health-check из телеметрии исключён и спана не даст,
# даже если SDK включён, — на нём «0 спанов» ничего бы не доказывал.
ids = [client.get("/").headers["X-Trace-ID"] for _ in range(2)]
# Маркер: остальной stdout процесса — JSON-записи логов (configure_logging),
# числовая строка одна, но не единственная.
print("RESULT", len(exporter.get_finished_spans()), *ids)
"""


def test_with_tracing_disabled_the_trace_id_is_random_in_the_same_format() -> None:
    """Настоящий `OTEL_SDK_DISABLED=true`: спанов нет, `X-Trace-ID` есть и у каждого свой."""
    env = {**os.environ, "OTEL_SDK_DISABLED": "true", "OTEL_TRACES_EXPORTER": "none"}
    result = subprocess.run(
        [sys.executable, "-c", _DISABLED_SCRIPT],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    [line] = [
        line for line in result.stdout.splitlines() if line.startswith("RESULT")
    ]
    _, finished, first, second = line.split()
    assert finished == "0"  # трассировка действительно выключена
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert re.fullmatch(r"[0-9a-f]{32}", second)
    assert first != second


def test_outgoing_requests_do_not_carry_our_trace_context() -> None:
    """Глобальный пропагатор пуст: httpx не отдаёт trace id GitHub и провайдерам модели."""
    create_app(SETTINGS)
    carrier: dict[str, str] = {}

    propagate.inject(carrier)

    assert carrier == {}


# --- SSE: инструментирование не копит поток -------------------------------------

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
_RUN = ReviewRun(
    id=RUN_ID,
    merge_request_id=UUID(int=2),
    head_sha="abc123",
    status=S.QUEUED,
    trigger=TriggerSource.WEBHOOK,
    last_progress_at=NOW,
    created_at=NOW,
    updated_at=NOW,
)


def test_sse_events_reach_the_client_as_they_happen(
    spans: InMemorySpanExporter,
) -> None:
    """Событие уходит клиенту до того, как прогон сменит состояние, а не одним куском в конце.

    ASGI-приложение гоняется напрямую: `TestClient` собирает тело целиком и разницы
    не увидел бы. Ожидание между проверками стоит, пока тест его не отпустит.
    """
    app = create_app(SETTINGS)
    current = {"run": _RUN}
    release = asyncio.Event()

    async def held_sleep(seconds: float) -> None:
        await release.wait()
        release.clear()

    app.dependency_overrides[get_run_reader] = lambda: lambda run_id: current["run"]
    app.dependency_overrides[get_sleep] = lambda: held_sleep
    cookies = sign_in_with_session(app, FakeUnitOfWork())
    cookie = "; ".join(f"{name}={value}" for name, value in cookies.items())
    path = f"/api/v1/reviews/{RUN_ID}/events"
    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"host", b"testserver"), (b"cookie", cookie.encode())],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    sent: list[Message] = []
    first_status = asyncio.Event()

    async def receive() -> Message:
        await asyncio.Event().wait()  # клиент не отключается
        raise AssertionError

    async def send(message: Message) -> None:
        sent.append(message)
        if b'"status":"queued"' in message.get("body", b""):
            first_status.set()

    def body() -> bytes:
        return b"".join(
            m.get("body", b"") for m in sent if m["type"] == "http.response.body"
        )

    async def scenario() -> None:
        stream = asyncio.create_task(app(scope, receive, send))
        await asyncio.wait_for(first_status.wait(), timeout=5)
        # Прогон ещё идёт: первое событие уже у клиента, поток не закрыт.
        assert b'"status":"completed"' not in body()
        assert sent[-1].get("more_body") is True
        assert not [s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER]
        current["run"] = replace(_RUN, status=S.COMPLETED)
        release.set()
        await asyncio.wait_for(stream, timeout=5)

    asyncio.run(scenario())

    assert b'"status":"completed"' in body()
    (start,) = [m for m in sent if m["type"] == "http.response.start"]
    headers = dict(start["headers"])
    assert headers[b"x-trace-id"].decode() == _trace_id(_server_span(spans))


# --- ни один ответ API не строится из телеметрии ---------------------------------

API = Path(__file__).parents[2] / "app" / "api"
# Единственные модули HTTP-слоя, которые знают о трассировке: фабрика ставит
# провайдер, middleware берёт trace id для заголовка и тела ошибки.
_TELEMETRY_USERS = {"factory.py", "trace_id.py"}


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def test_no_api_response_is_built_from_telemetry() -> None:
    """Ответы ручек — из базы: роуты не читают ни OpenTelemetry, ни модуль телеметрии."""
    offenders = {
        path.name
        for path in API.glob("*.py")
        if path.name not in _TELEMETRY_USERS
        and any(
            name.startswith(("opentelemetry", "app.infrastructure.telemetry"))
            for name in _imports(path)
        )
    }

    assert offenders == set()
