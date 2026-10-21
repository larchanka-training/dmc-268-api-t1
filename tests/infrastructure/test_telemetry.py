"""Адаптеры трассировки: спаны этапов и контекст в заголовках AMQP. Без брокера и сети.

Спаны пишет в память провайдер из `tests/conftest.py` (фикстура `spans`).
"""

import io
import logging
from typing import Any
from uuid import UUID

import pika
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext, SpanKind, StatusCode

from app.domain.entities import ReviewJob
from app.domain.enums import ReviewRunStage
from app.infrastructure import telemetry
from app.infrastructure.queue.rabbitmq import RabbitMQJobQueue, consuming_span
from app.infrastructure.telemetry import (
    OtelStageTracer,
    build_tracer_provider,
    current_trace_id,
)

RUN_ID = UUID(int=2)
JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=RUN_ID,
    event_type="pull_request",
    action="opened",
    installation_id=512804923,
    repository_provider_id="987654",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="abc123def",
    base_sha="fed654cba",
)


def _context(span: ReadableSpan) -> SpanContext:
    context = span.get_span_context()
    assert context is not None
    return context


def test_a_stage_span_carries_the_run_id_and_is_current_while_the_stage_runs(
    spans: InMemorySpanExporter,
) -> None:
    span = OtelStageTracer().start(RUN_ID, ReviewRunStage.ANALYSING)
    inside = trace.get_current_span()
    span.end(None)

    (finished,) = spans.get_finished_spans()
    assert finished.name == "review.analysing"
    assert finished.attributes is not None
    assert finished.attributes["review_run_id"] == str(RUN_ID)
    assert finished.attributes["review.stage"] == "analysing"
    assert inside.get_span_context() == finished.get_span_context()
    assert finished.status.status_code is StatusCode.UNSET
    # После закрытия этапа текущего спана нет: контекст снят.
    assert not trace.get_current_span().get_span_context().is_valid


def test_a_failed_stage_span_has_the_error_status_and_code(
    spans: InMemorySpanExporter,
) -> None:
    OtelStageTracer().start(RUN_ID, ReviewRunStage.FETCHING_DIFF).end("SCM_UNAVAILABLE")

    (finished,) = spans.get_finished_spans()
    assert finished.status.status_code is StatusCode.ERROR
    assert finished.attributes is not None
    assert finished.attributes["error.type"] == "SCM_UNAVAILABLE"


class _Channel:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    def basic_publish(self, **kwargs: Any) -> None:
        self.published.append(kwargs)


def _published_properties(monkeypatch: pytest.MonkeyPatch) -> pika.BasicProperties:
    queue = RabbitMQJobQueue("amqp://guest:guest@localhost//")
    channel = _Channel()
    monkeypatch.setattr(queue, "_channel_ready", lambda: channel)
    queue.enqueue(JOB)
    (message,) = channel.published
    properties: pika.BasicProperties = message["properties"]
    return properties


def test_the_worker_continues_the_trace_of_the_publish(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    properties = _published_properties(monkeypatch)
    assert set(properties.headers or {}) == {"traceparent"}

    with consuming_span(properties):
        trace_id_inside = current_trace_id()

    publish, process = spans.get_finished_spans()
    assert (publish.kind, process.kind) == (SpanKind.PRODUCER, SpanKind.CONSUMER)
    assert publish.attributes is not None
    assert publish.attributes["review_run_id"] == str(RUN_ID)
    assert process.parent is not None
    assert process.parent.span_id == _context(publish).span_id
    assert trace_id_inside == trace.format_trace_id(_context(publish).trace_id)


def test_the_message_body_stays_domain_data_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = RabbitMQJobQueue("amqp://guest:guest@localhost//")
    channel = _Channel()
    monkeypatch.setattr(queue, "_channel_ready", lambda: channel)

    queue.enqueue(JOB)

    assert b"traceparent" not in channel.published[0]["body"]


@pytest.mark.parametrize(
    "properties",
    [
        None,
        pika.BasicProperties(),
        pika.BasicProperties(headers={"traceparent": "мусор"}),
    ],
)
def test_a_message_without_a_valid_traceparent_starts_a_new_trace(
    spans: InMemorySpanExporter, properties: pika.BasicProperties | None
) -> None:
    with consuming_span(properties):
        pass

    (process,) = spans.get_finished_spans()
    assert process.parent is None


def test_a_traceparent_header_in_bytes_is_read_too(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = _published_properties(monkeypatch).headers or {}
    as_bytes = pika.BasicProperties(
        headers={"traceparent": headers["traceparent"].encode()}
    )

    with consuming_span(as_bytes):
        pass

    publish, process = spans.get_finished_spans()
    assert process.parent is not None
    assert process.parent.span_id == _context(publish).span_id


# --- экспорт ----------------------------------------------------------------------


@pytest.fixture
def console(monkeypatch: pytest.MonkeyPatch) -> io.StringIO:
    """Вывод консольного экспортёра.

    Экспортёр запоминает `sys.stdout` при импорте SDK — раньше, чем pytest подменяет
    вывод теста, поэтому поток подставляется явно.
    """
    out = io.StringIO()
    monkeypatch.setattr(
        telemetry, "ConsoleSpanExporter", lambda: ConsoleSpanExporter(out=out)
    )
    return out


def _emit(provider: TracerProvider) -> None:
    provider.get_tracer("test").start_span("export-check").end()
    provider.force_flush()


def test_without_configuration_spans_go_nowhere(
    monkeypatch: pytest.MonkeyPatch,
    console: io.StringIO,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv("OTEL_TRACES_EXPORTER", raising=False)
    provider = build_tracer_provider("dmc-268-api")

    _emit(provider)

    assert console.getvalue() == ""
    assert caplog.records == []
    assert provider.resource.attributes["service.name"] == "dmc-268-api"


def test_the_console_exporter_prints_spans(
    monkeypatch: pytest.MonkeyPatch, console: io.StringIO
) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")

    _emit(build_tracer_provider("dmc-268-api"))

    assert '"name": "export-check"' in console.getvalue()


def test_an_unsupported_exporter_warns_once_and_exports_nothing(
    monkeypatch: pytest.MonkeyPatch,
    console: io.StringIO,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # `fileConfig` в тестах Alembic отключает уже созданные логгеры — как в
    # tests/test_problem_details.py.
    monkeypatch.setattr(
        logging.getLogger("app.infrastructure.telemetry"), "disabled", False
    )
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "otlp")

    _emit(build_tracer_provider("dmc-268-api"))

    [record] = caplog.records
    assert record.levelname == "WARNING"
    assert "otlp" in record.getMessage()
    assert console.getvalue() == ""
