"""Трассировка HTTP API: `X-Trace-ID` — trace id спана запроса. Без базы, брокера и сети.

Спаны пишет в память провайдер из `tests/conftest.py` (фикстура `spans`).
"""

import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import propagate
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, format_span_id, format_trace_id

from app.api import trace_id as trace_id_module
from app.api.dependencies import get_unit_of_work_factory
from app.api.factory import create_app
from app.config import Settings

from ..fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)
ORIGIN = "http://localhost:5173"
FOREIGN_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
FOREIGN_SPAN_ID = "00f067aa0ba902b7"
FOREIGN_TRACEPARENT = f"00-{FOREIGN_TRACE_ID}-{FOREIGN_SPAN_ID}-01"


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
    response = TestClient(_app()).get("/health")

    assert response.status_code == 200
    assert response.headers["X-Trace-ID"] == _trace_id(_server_span(spans))


def test_a_failure_to_parse_traceparent_still_gives_a_problem_with_the_trace_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`trace_id` в состоянии запроса раньше разбора заголовка: обработчику `500` он нужен."""

    def broken(traceparent: str | None) -> None:
        raise RuntimeError("разбор traceparent упал")

    monkeypatch.setattr(trace_id_module, "link_client_trace", broken)

    response = TestClient(_app(), raise_server_exceptions=False).get(
        "/health", headers={"traceparent": FOREIGN_TRACEPARENT}
    )

    assert response.status_code == 500
    assert re.fullmatch(r"[0-9a-f]{32}", response.json()["trace_id"])


def test_a_valid_traceparent_becomes_a_link_not_the_parent(
    spans: InMemorySpanExporter,
) -> None:
    """Серверный спан — корень нового трейса с одной ссылкой на контекст клиента."""
    response = TestClient(_app()).get(
        "/health",
        headers={
            "traceparent": FOREIGN_TRACEPARENT,
            "tracestate": "vendor=secret",
            "X-Trace-ID": "f" * 32,
        },
    )

    server = _server_span(spans)
    assert server.parent is None  # запрос начал новый трейс
    assert _trace_id(server) != FOREIGN_TRACE_ID
    (link,) = server.links
    assert format_trace_id(link.context.trace_id) == FOREIGN_TRACE_ID
    assert format_span_id(link.context.span_id) == FOREIGN_SPAN_ID
    assert link.context.is_remote
    assert dict(link.context.trace_state) == {}  # `tracestate` не читаем
    assert dict(link.attributes or {}) == {"link.source": "client"}
    assert response.headers["X-Trace-ID"] == _trace_id(server)


def test_x_trace_id_is_the_trace_id_of_the_server_span_not_of_the_client(
    spans: InMemorySpanExporter,
) -> None:
    response = TestClient(_app(), raise_server_exceptions=False).get(
        "/api/v1/_boom", headers={"traceparent": FOREIGN_TRACEPARENT}
    )

    server = _server_span(spans)
    assert response.headers["X-Trace-ID"] == _trace_id(server) != FOREIGN_TRACE_ID
    assert response.json()["trace_id"] == _trace_id(server)


def test_a_not_sampled_traceparent_is_recorded_like_a_request_without_it(
    spans: InMemorySpanExporter,
) -> None:
    """Флаг `00` клиента не выключает запись: сэмплер решает как для запроса без заголовка."""
    client = TestClient(_app())
    client.get("/health")
    plain = _server_span(spans)
    spans.clear()

    client.get("/health", headers={"traceparent": FOREIGN_TRACEPARENT[:-2] + "00"})

    linked = _server_span(spans)
    assert linked.parent is None and plain.parent is None
    assert linked.context is not None and plain.context is not None
    assert linked.context.trace_flags == plain.context.trace_flags
    assert linked.context.trace_flags.sampled
    (link,) = linked.links
    assert not link.context.trace_flags.sampled  # ссылка хранит флаг клиента как есть


@pytest.mark.parametrize(
    "traceparent",
    [
        "not-a-traceparent",
        f"00-{'0' * 32}-00f067aa0ba902b7-01",  # нулевой trace id
        f"00-{FOREIGN_TRACE_ID}-{'0' * 16}-01",  # нулевой span id
        f"ff-{FOREIGN_TRACE_ID}-00f067aa0ba902b7-01",  # запрещённая версия
        f"00-{FOREIGN_TRACE_ID[:-1]}-00f067aa0ba902b7-01",  # короткий trace id
        "",
    ],
)
def test_an_invalid_traceparent_gives_no_link_and_the_same_response(
    spans: InMemorySpanExporter, traceparent: str
) -> None:
    client = TestClient(_app())
    expected = client.get("/health")
    spans.clear()

    response = client.get("/health", headers={"traceparent": traceparent})

    server = _server_span(spans)
    assert server.parent is None
    assert server.links == ()
    assert response.status_code == expected.status_code
    assert response.json() == expected.json()
    assert response.headers["X-Trace-ID"] == _trace_id(server)


# Сэмплер берётся из `OTEL_TRACES_SAMPLER` при создании провайдера, а в этом процессе
# провайдер — из `tests/conftest.py`. Поэтому проверка сэмплера — в отдельном процессе,
# где провайдер ставит сам `configure_tracing`.
_SAMPLER_SCRIPT = """
import sys

from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind

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
response = TestClient(app).get("/health", headers={"traceparent": sys.argv[1]})
server = [s for s in exporter.get_finished_spans() if s.kind is SpanKind.SERVER]
print(len(server), response.headers["X-Trace-ID"])
"""


@pytest.mark.parametrize(
    ("sampler", "flags", "recorded"),
    [
        ("parentbased_always_off", "01", 0),  # `01` клиента не включает запись
        ("parentbased_always_on", "00", 1),  # `00` клиента её не выключает
    ],
)
def test_the_parentbased_sampler_ignores_the_client_context(
    sampler: str, flags: str, recorded: int
) -> None:
    env = {**os.environ, "OTEL_TRACES_SAMPLER": sampler, "OTEL_TRACES_EXPORTER": "none"}
    env.pop("OTEL_SDK_DISABLED", None)
    result = subprocess.run(
        [sys.executable, "-c", _SAMPLER_SCRIPT, FOREIGN_TRACEPARENT[:-2] + flags],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    finished, trace_id = result.stdout.split()
    assert int(finished) == recorded
    assert re.fullmatch(r"[0-9a-f]{32}", trace_id)
    assert trace_id != FOREIGN_TRACE_ID


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
    TestClient(_app()).get("/health", params={"page": "2"})

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
ids = [client.get("/health").headers["X-Trace-ID"] for _ in range(2)]
print(len(exporter.get_finished_spans()), *ids)
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
    finished, first, second = result.stdout.split()
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
