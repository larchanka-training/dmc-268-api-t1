"""Адаптеры трассировки: спаны этапов и контекст в заголовках AMQP. Без брокера и сети.

Спаны пишет в память провайдер из `tests/conftest.py` (фикстура `spans`).
"""

import io
import logging
import os
import sys
import threading
import types
import warnings
from collections.abc import Iterator
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from uuid import UUID

import httpx
import pika
import pytest
from fastapi.testclient import TestClient
from opentelemetry import _logs as otel_logs
from opentelemetry import trace
from opentelemetry._logs import SeverityNumber
from opentelemetry._logs import _internal as logs_internal
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation._semconv import (
    _OpenTelemetrySemanticConventionStability,
)
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import engine as sqlalchemy_engine
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    ConsoleLogRecordExporter,
    InMemoryLogRecordExporter,
)
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    Histogram,
    InMemoryMetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext, SpanKind, StatusCode
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.pool import QueuePool, StaticPool

from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import ReviewJob
from app.domain.enums import ReviewRunStage
from app.domain.llm_errors import LlmTimeoutError
from app.infrastructure import telemetry
from app.infrastructure.container import build_container
from app.infrastructure.queue.rabbitmq import RabbitMQJobQueue, consuming_span
from app.infrastructure.telemetry import (
    OtelStageTracer,
    build_tracer_provider,
    current_trace_id,
)
from app.worker import factory as worker_factory

from ..application.test_review_pipeline import _run, _seed_run
from ..fakes.llm_gateway import ScriptedLlmGateway
from ..fakes.unit_of_work import FakeUnitOfWork
from ..metrics import metric_delta, metric_histogram, metric_snapshot

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

    with consuming_span(properties, delivery_tag=1):
        trace_id_inside = current_trace_id()

    publish, process = spans.get_finished_spans()
    assert (publish.kind, process.kind) == (SpanKind.PRODUCER, SpanKind.CONSUMER)
    assert publish.attributes is not None
    assert publish.attributes["review_run_id"] == str(RUN_ID)
    assert process.parent is not None
    assert process.parent.span_id == _context(publish).span_id
    assert trace_id_inside == trace.format_trace_id(_context(publish).trace_id)


def test_queue_spans_follow_the_messaging_semantic_conventions(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    with consuming_span(_published_properties(monkeypatch), delivery_tag=7):
        pass

    publish, process = spans.get_finished_spans()
    destination = {
        "messaging.system": "rabbitmq",
        "messaging.destination.name": "review_jobs",
        "messaging.rabbitmq.destination.routing_key": "review_jobs",
    }
    assert publish.name == "publish review_jobs"
    assert dict(publish.attributes or {}) == {
        **destination,
        "messaging.operation.name": "publish",
        "messaging.operation.type": "send",
        "review_run_id": str(RUN_ID),
    }
    assert process.name == "process review_jobs"
    assert dict(process.attributes or {}) == {
        **destination,
        "messaging.operation.name": "process",
        "messaging.operation.type": "process",
        "messaging.rabbitmq.message.delivery_tag": 7,
        # Без `basic.deliver` в руках — первая попытка: повторной доставки нет.
        "messaging.rabbitmq.message.redelivered": False,
    }


@pytest.mark.parametrize("redelivered", [False, True])
def test_the_process_span_marks_redelivery_as_a_boolean(
    spans: InMemorySpanExporter,
    monkeypatch: pytest.MonkeyPatch,
    redelivered: bool,
) -> None:
    """`messaging.rabbitmq.message.redelivered` — всегда булев: попытки различимы в хранилище."""
    with consuming_span(
        _published_properties(monkeypatch), delivery_tag=1, redelivered=redelivered
    ):
        pass

    (_, process) = spans.get_finished_spans()
    assert process.attributes is not None
    value = process.attributes["messaging.rabbitmq.message.redelivered"]
    assert value is redelivered


class _StaleChannel(_Channel):
    def basic_publish(self, **kwargs: Any) -> None:
        raise pika.exceptions.StreamLostError("broker closed the idle connection")


def _publish_through(
    monkeypatch: pytest.MonkeyPatch, *channels: _Channel
) -> RabbitMQJobQueue:
    queue = RabbitMQJobQueue("amqp://guest:guest@localhost//")
    pending = list(channels)
    monkeypatch.setattr(queue, "_channel_ready", lambda: pending[0])
    monkeypatch.setattr(queue, "_drop_connection", lambda: pending.pop(0))
    return queue


def test_a_publish_that_survives_a_reconnect_is_one_span_without_an_error(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel = _Channel()
    _publish_through(monkeypatch, _StaleChannel(), channel).enqueue(JOB)

    (publish,) = spans.get_finished_spans()
    assert publish.status.status_code is StatusCode.UNSET
    # Повтор несёт тот же traceparent: воркер продолжит этот спан.
    (message,) = channel.published
    traceparent = message["properties"].headers["traceparent"]
    assert traceparent.split("-")[2] == trace.format_span_id(_context(publish).span_id)


def test_a_publish_that_fails_twice_is_one_span_with_an_error(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = _publish_through(monkeypatch, _StaleChannel(), _StaleChannel())

    with pytest.raises(pika.exceptions.StreamLostError):
        queue.enqueue(JOB)

    (publish,) = spans.get_finished_spans()
    assert publish.status.status_code is StatusCode.ERROR


def test_the_message_body_stays_domain_data_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = RabbitMQJobQueue("amqp://guest:guest@localhost//")
    channel = _Channel()
    monkeypatch.setattr(queue, "_channel_ready", lambda: channel)

    queue.enqueue(JOB)

    assert b"traceparent" not in channel.published[0]["body"]


@pytest.mark.parametrize("error", [ValueError("сбой"), KeyboardInterrupt(), SystemExit(1)])
def test_a_failure_of_processing_marks_the_process_span_as_an_error(
    spans: InMemorySpanExporter, error: BaseException
) -> None:
    """И `Exception`, и `KeyboardInterrupt`/`SystemExit`: ERROR, событие, исключение наружу."""
    with pytest.raises(type(error)), consuming_span(None, delivery_tag=1):
        raise error

    (process,) = spans.get_finished_spans()
    assert process.status.status_code is StatusCode.ERROR
    (event,) = process.events
    assert event.name == "exception"
    assert event.attributes is not None
    assert event.attributes["exception.type"] == type(error).__name__


@pytest.mark.parametrize(
    "error",
    [ValueError("HTTP 400: def leaked_from_diff(): ..."), SystemExit("код из диффа")],
)
def test_the_status_of_a_failed_process_span_names_only_the_exception_type(
    spans: InMemorySpanExporter, error: BaseException
) -> None:
    """Описание статуса редакцию коллектора не проходит — текст только в событии."""
    with pytest.raises(type(error)), consuming_span(None, delivery_tag=1):
        raise error

    (process,) = spans.get_finished_spans()
    assert process.status.description == type(error).__name__
    (event,) = process.events
    assert event.attributes is not None
    assert event.attributes["exception.message"] == str(error)


def test_a_caught_exception_marks_the_current_span_with_its_type_only(
    spans: InMemorySpanExporter,
) -> None:
    with trace.get_tracer("test").start_as_current_span("caught"):
        telemetry.record_current_exception(RuntimeError("DETAIL: Key (login)=(octocat)"))

    (span,) = spans.get_finished_spans()
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "RuntimeError"
    (event,) = span.events
    assert event.attributes is not None
    assert event.attributes["exception.message"] == "DETAIL: Key (login)=(octocat)"


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
    with consuming_span(properties, delivery_tag=1):
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

    with consuming_span(as_bytes, delivery_tag=1):
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


def test_without_configuration_every_trace_is_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OTEL_TRACES_SAMPLER", raising=False)

    sampler = build_tracer_provider("dmc-268-api").sampler

    assert sampler.get_description().startswith("ParentBased{root:AlwaysOnSampler")


@pytest.mark.parametrize("ratio", ["traceidratio", "parentbased_traceidratio"])
def test_a_ratio_sampler_is_not_applied_and_warned_about(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, ratio: str
) -> None:
    """`X-Trace-ID` видят люди: по каждому trace id должен находиться трейс."""
    monkeypatch.setattr(
        logging.getLogger("app.infrastructure.telemetry"), "disabled", False
    )
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", ratio)
    monkeypatch.setenv("OTEL_TRACES_SAMPLER_ARG", "0.1")

    sampler = build_tracer_provider("dmc-268-api").sampler

    assert sampler.get_description().startswith("ParentBased{root:AlwaysOnSampler")
    (record,) = [r for r in caplog.records if "OTEL_TRACES_SAMPLER" in r.getMessage()]
    assert record.levelno == logging.WARNING


def test_a_sampler_without_a_ratio_is_still_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "parentbased_always_off")

    sampler = build_tracer_provider("dmc-268-api").sampler

    assert sampler.get_description().startswith("ParentBased{root:AlwaysOffSampler")


def test_the_console_exporter_prints_spans(
    monkeypatch: pytest.MonkeyPatch, console: io.StringIO
) -> None:
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console")

    _emit(build_tracer_provider("dmc-268-api"))

    assert '"name": "export-check"' in console.getvalue()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("console,otlp", ["console", "otlp"]),
        (" Console , OTLP ", ["console", "otlp"]),
        ("console,console,otlp,otlp", ["console", "otlp"]),
        # Мусор остаётся в списке: функция чистая, поддержанность решает провайдер.
        ("console,zipkin", ["console", "zipkin"]),
        ("", []),
        (" , , ", []),
    ],
)
def test_parse_exporters_normalizes_the_list(value: str, expected: list[str]) -> None:
    assert telemetry.parse_exporters(value) == expected


def test_a_processor_is_added_per_supported_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`console,otlp` — по процессору на экспортёр: печать в stdout и OTLP/HTTP."""
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console,otlp")

    provider = build_tracer_provider("dmc-268-api")

    exporters = [
        processor.span_exporter
        for processor in provider._active_span_processor._span_processors
        if isinstance(processor, BatchSpanProcessor)
    ]
    assert [type(exporter) for exporter in exporters] == [
        ConsoleSpanExporter,
        OTLPSpanExporter,
    ]


def test_an_unsupported_value_turns_off_only_itself(
    monkeypatch: pytest.MonkeyPatch, console: io.StringIO, caplog: pytest.LogCaptureFixture
) -> None:
    """`console,zipkin`: console работает, про `zipkin` — одно предупреждение, спаны не теряются."""
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "console,zipkin")
    monkeypatch.setattr(
        logging.getLogger("app.infrastructure.telemetry"), "disabled", False
    )

    provider = build_tracer_provider("dmc-268-api")
    _emit(provider)

    [warning] = [r for r in caplog.records if r.levelname == "WARNING"]
    assert "zipkin" in warning.getMessage()
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
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "zipkin")

    _emit(build_tracer_provider("dmc-268-api"))

    [record] = caplog.records
    assert record.levelname == "WARNING"
    assert "zipkin" in record.getMessage()
    assert console.getvalue() == ""


def _span_with_long_attribute(provider: TracerProvider) -> str:
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    provider.get_tracer("test").start_span(
        "limit-check", attributes={"url.full": "x" * 10_000}
    ).end()
    (span,) = exporter.get_finished_spans()
    assert span.attributes is not None
    value = span.attributes["url.full"]
    assert isinstance(value, str)
    return value


def test_attribute_values_are_truncated_to_the_limit_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT", "10")

    value = _span_with_long_attribute(build_tracer_provider("dmc-268-api"))

    assert value == "x" * 10


def test_attribute_values_are_truncated_to_the_default_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Без переменной лимит задаёт код: раздувание `url.full` и текстов ошибок ограничено."""
    monkeypatch.delenv("OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT", raising=False)

    value = _span_with_long_attribute(build_tracer_provider("dmc-268-api"))

    assert value == "x" * 4096


def test_a_negative_attribute_length_limit_does_not_stop_the_start(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Отрицательный лимит не роняет старт: предупреждение и дефолт, как у мусора."""
    monkeypatch.setenv("OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT", "-5")
    monkeypatch.setattr(
        logging.getLogger("app.infrastructure.telemetry"), "disabled", False
    )

    value = _span_with_long_attribute(build_tracer_provider("dmc-268-api"))

    [warning] = [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and "OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT" in r.getMessage()
    ]
    assert "-5" in warning.getMessage()
    assert value == "x" * 4096


def test_the_span_resource_names_the_version_and_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`service.name` — из кода; `service.version` и окружение — из `OTEL_RESOURCE_ATTRIBUTES`.

    `Resource.create` мержит переменную сам: спан несёт всё сразу, окружение задаёт
    версию и среду (compose и `infra/`), код — только имя сервиса.
    """
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "service.version=abc123def,deployment.environment.name=local",
    )
    provider = build_tracer_provider("dmc-268-api")
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    provider.get_tracer("test").start_span("resource-check").end()

    (span,) = exporter.get_finished_spans()
    attributes = dict(span.resource.attributes)
    assert attributes["service.name"] == "dmc-268-api"
    assert attributes["service.version"] == "abc123def"
    assert attributes["deployment.environment.name"] == "local"


# --- экспорт метрик -----------------------------------------------------------------


def test_metrics_without_configuration_go_nowhere(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Без `OTEL_METRICS_EXPORTER` ридеров нет: метрики никуда не уходят, в логе тихо.

    Гейт обязателен: SDK по умолчанию сам ставит `otlp` — без него провайдер
    стучался бы в localhost:4318.
    """
    monkeypatch.delenv("OTEL_METRICS_EXPORTER", raising=False)

    provider = telemetry.build_meter_provider("dmc-268-api")

    assert provider._metric_readers == []
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_a_metric_reader_is_added_per_supported_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`console,otlp` — по ридеру на экспортёр: печать в stdout и OTLP/HTTP."""
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "console,otlp")

    provider = telemetry.build_meter_provider("dmc-268-api")

    readers = [
        reader
        for reader in provider._metric_readers
        if isinstance(reader, PeriodicExportingMetricReader)
    ]
    assert [type(reader._exporter) for reader in readers] == [
        ConsoleMetricExporter,
        OTLPMetricExporter,
    ]


def test_an_unsupported_metrics_exporter_warns_once_and_exports_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Мусорное значение выключает только свой экспортёр и предупреждает один раз."""
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "zipkin")
    monkeypatch.setattr(
        logging.getLogger("app.infrastructure.telemetry"), "disabled", False
    )

    provider = telemetry.build_meter_provider("dmc-268-api")

    [record] = caplog.records
    assert record.levelname == "WARNING"
    assert "zipkin" in record.getMessage()
    assert provider._metric_readers == []


def test_the_metric_resource_names_the_version_and_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resource метрик — тот же, что у трейсов: имя из кода, версия и среда — из окружения."""
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "service.version=abc123def,deployment.environment.name=local",
    )
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "otlp")
    reader = InMemoryMetricReader()
    monkeypatch.setattr(telemetry, "PeriodicExportingMetricReader", lambda exporter: reader)

    provider = telemetry.build_meter_provider("dmc-268-api")
    provider.get_meter("test").create_counter("resource-check").add(1)

    data = reader.get_metrics_data()
    assert data is not None
    (resource_metrics,) = data.resource_metrics
    attributes = dict(resource_metrics.resource.attributes)
    assert attributes["service.name"] == "dmc-268-api"
    assert attributes["service.version"] == "abc123def"
    assert attributes["deployment.environment.name"] == "local"


# --- экспорт логов -------------------------------------------------------------------


@pytest.fixture
def logs_gate() -> Iterator[None]:
    """Гейт логов возвращён в исходное состояние после теста.

    `set_logger_provider` ставит глобального провайдера один раз на процесс
    (одноразовый `Once` внутри `opentelemetry._logs`), поэтому тест, включивший
    провайдер, обязан вернуть и провайдера, и хендлер на root — иначе логи
    остальных тестов пошли бы в его экспортёр, а гейт `none` перестал бы быть
    проверяемым. Провайдер закрывается до сброса: батч-процессор держит поток.
    Глобалы возвращаются присваиванием, а не `monkeypatch`-ом: тот вернул бы
    свои значения на undo — после teardown, то есть следующему тесту.
    """
    yield
    if telemetry._logger_installed is not None:
        telemetry._logger_installed.shutdown()
    telemetry._logger_installed = None
    if telemetry._log_handler is not None:
        logging.getLogger().removeHandler(telemetry._log_handler)
    telemetry._log_handler = None
    logs_internal._LOGGER_PROVIDER = None
    logs_internal._LOGGER_PROVIDER_SET_ONCE._done = False


def test_logs_without_configuration_go_nowhere(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Без `OTEL_LOGS_EXPORTER` процессоров нет: логи идут только в stdout-JSON."""
    monkeypatch.delenv("OTEL_LOGS_EXPORTER", raising=False)

    provider = telemetry.build_logger_provider("dmc-268-api")

    assert provider._multi_log_record_processor._log_record_processors == ()
    assert [r for r in caplog.records if r.levelname == "WARNING"] == []


def test_a_log_processor_is_added_per_supported_exporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`console,otlp` — по процессору на экспортёр: печать в stdout и OTLP/HTTP."""
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "console,otlp")

    provider = telemetry.build_logger_provider("dmc-268-api")

    processors = provider._multi_log_record_processor._log_record_processors
    exporters = [
        type(processor._batch_processor._exporter)
        for processor in processors
        if isinstance(processor, BatchLogRecordProcessor)
    ]
    assert exporters == [ConsoleLogRecordExporter, OTLPLogExporter]


def test_an_unsupported_logs_exporter_warns_once_and_exports_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Мусорное значение выключает только свой экспортёр и предупреждает один раз."""
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "zipkin")
    monkeypatch.setattr(
        logging.getLogger("app.infrastructure.telemetry"), "disabled", False
    )

    provider = telemetry.build_logger_provider("dmc-268-api")

    [record] = caplog.records
    assert record.levelname == "WARNING"
    assert "zipkin" in record.getMessage()
    assert provider._multi_log_record_processor._log_record_processors == ()


def test_the_log_resource_names_the_version_and_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resource логов — тот же, что у трейсов: имя из кода, версия и среда — из окружения."""
    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "service.version=abc123def,deployment.environment.name=local",
    )
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "otlp")
    exporter = InMemoryLogRecordExporter()
    monkeypatch.setattr(telemetry, "OTLPLogExporter", lambda: exporter)

    provider = telemetry.build_logger_provider("dmc-268-api")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        handler = LoggingHandler(logger_provider=provider)
    root = logging.getLogger()
    root.addHandler(handler)
    level = root.level
    root.setLevel(logging.INFO)
    try:
        root.info("resource-check")
    finally:
        root.setLevel(level)
        root.removeHandler(handler)
    provider.force_flush()

    (record,) = exporter.get_finished_logs()
    attributes = dict(record.resource.attributes)
    assert attributes["service.name"] == "dmc-268-api"
    assert attributes["service.version"] == "abc123def"
    assert attributes["deployment.environment.name"] == "local"


def test_the_otlp_log_exporter_timeout_is_in_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`OTEL_EXPORTER_OTLP_TIMEOUT=4` — таймаут экспортёра логов ровно 4 секунды.

    Спека OTel определяет эту переменную в миллисекундах (4000 = 4 с), но Python
    SDK читает её в секундах: `_resolve_timeout` возвращает `float(raw)` без
    пересчёта, и значение идёт в `time.time() + timeout` и в таймаут транспорта.
    Поэтому compose и `infra/main.tf` ставят 4, а не 4000: при секундной
    семантике 4000 — это 66 минут зависания одной попытки экспорта. Ассерт —
    на фактический атрибут клиента экспортёра: если SDK перейдёт на
    миллисекунды спеки, тест упадёт вместе со значением в конфигах — править
    синхронно.
    """
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TIMEOUT", "4")
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "otlp")

    provider = telemetry.build_logger_provider("dmc-268-api")
    (processor,) = provider._multi_log_record_processor._log_record_processors
    assert isinstance(processor, BatchLogRecordProcessor)
    exporter = processor._batch_processor._exporter
    assert isinstance(exporter, OTLPLogExporter)

    assert exporter._client._timeout == 4.0


def test_the_logs_gate_default_installs_nothing(
    monkeypatch: pytest.MonkeyPatch, logs_gate: None
) -> None:
    """Дефолт `OTEL_LOGS_EXPORTER` (`none`): ни провайдера, ни хендлера на root.

    Провайдер остаётся прокси — установку без экспортёров нельзя было бы
    отозвать, а stdout-JSON и так полный сигнал (docker logs, отладка).
    """
    monkeypatch.delenv("OTEL_LOGS_EXPORTER", raising=False)

    telemetry.configure_telemetry(telemetry.API_SERVICE)

    assert not isinstance(otel_logs.get_logger_provider(), LoggerProvider)
    assert not any(
        isinstance(handler, LoggingHandler) for handler in logging.getLogger().handlers
    )


def test_the_otlp_gate_installs_the_provider_and_the_handler_once(
    monkeypatch: pytest.MonkeyPatch, logs_gate: None
) -> None:
    """`OTEL_LOGS_EXPORTER=otlp`: провайдер и хендлер ставятся; повторный
    `configure_telemetry` не плодит ни того, ни другого."""
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "otlp")
    exporter = InMemoryLogRecordExporter()
    monkeypatch.setattr(telemetry, "OTLPLogExporter", lambda: exporter)

    telemetry.configure_telemetry(telemetry.API_SERVICE)
    provider = otel_logs.get_logger_provider()

    telemetry.configure_telemetry(telemetry.WORKER_SERVICE)

    assert isinstance(provider, LoggerProvider)
    assert otel_logs.get_logger_provider() is provider
    assert dict(provider.resource.attributes)["service.name"] == telemetry.API_SERVICE
    handlers = [
        handler for handler in logging.getLogger().handlers if isinstance(handler, LoggingHandler)
    ]
    assert len(handlers) == 1


def test_the_otlp_log_handler_delivers_body_severity_and_exception_attributes(
    monkeypatch: pytest.MonkeyPatch, logs_gate: None
) -> None:
    """Запись Python logging уходит по OTLP: body — сообщение, уровень — severity,
    при `exc_info` — атрибуты `exception.*`.

    Именно `exception.message`, `exception.stacktrace` и тело гасит редакция
    коллектора (`redact_logs`), `exception.type` остаётся диагнозом записи —
    тот же паритет, что у stdout-JSON (`configure_logging`)."""
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "otlp")
    exporter = InMemoryLogRecordExporter()
    monkeypatch.setattr(telemetry, "OTLPLogExporter", lambda: exporter)
    telemetry.configure_telemetry(telemetry.API_SERVICE)
    root = logging.getLogger()
    level = root.level
    root.setLevel(logging.INFO)
    try:
        root.info("привет %s", "мир")
        try:
            raise ValueError("сбой этапа")
        except ValueError as error:
            root.error("упало", exc_info=error)
    finally:
        root.setLevel(level)
    provider = otel_logs.get_logger_provider()
    assert isinstance(provider, LoggerProvider)
    provider.force_flush()

    info, failed = (data.log_record for data in exporter.get_finished_logs())
    assert info.body == "привет мир"
    assert info.severity_number is SeverityNumber.INFO
    assert info.severity_text == "INFO"
    assert failed.body == "упало"
    assert failed.severity_number is SeverityNumber.ERROR
    attributes = dict(failed.attributes or {})
    assert attributes["exception.type"] == "ValueError"
    assert attributes["exception.message"] == "сбой этапа"
    stacktrace = attributes["exception.stacktrace"]
    assert isinstance(stacktrace, str)
    assert "ValueError: сбой этапа" in stacktrace


# --- бизнес-метрики -----------------------------------------------------------------


def test_stage_durations_land_in_the_histogram(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Гистограмму этапов пишет `OtelStageTracer` при закрытии этапа — и для упавшего."""
    before = metric_snapshot(metric_reader)
    tracer = OtelStageTracer()

    tracer.start(RUN_ID, ReviewRunStage.ANALYSING).end(None)
    tracer.start(RUN_ID, ReviewRunStage.FETCHING_DIFF).end("SCM_UNAVAILABLE")

    count, total = metric_delta(
        metric_reader, before, "review_stage_duration", stage="analysing"
    )
    assert count == 1
    assert total is not None and total > 0
    count, total = metric_delta(
        metric_reader, before, "review_stage_duration", stage="fetching_diff"
    )
    assert count == 1
    assert total is not None and total > 0


def _total_observations(reader: InMemoryMetricReader, name: str) -> float:
    """Число наблюдений метрики по всем рядам: каждый запрос пишет ровно в один."""
    data = reader.get_metrics_data()
    if data is None:
        return 0.0
    total = 0.0
    for resource_metrics in data.resource_metrics or ():
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == name and isinstance(metric.data, Histogram):
                    total += sum(float(point.count) for point in metric.data.data_points)
    return total


def test_api_requests_produce_http_metrics(
    metric_reader: InMemoryMetricReader,
) -> None:
    """HTTP-метрики — встроенная телеметрия FastAPI: берёт глобальный провайдер сама.

    Ручки `/metrics` нет — метрики уходит коллектору, а не скрейпятся с сервиса.
    """
    app = create_app(SETTINGS)
    with TestClient(app) as client:
        assert client.get("/metrics").status_code == HTTPStatus.NOT_FOUND
        before = _total_observations(metric_reader, "http.server.request.duration")
        client.get("/")

    assert _total_observations(metric_reader, "http.server.request.duration") == before + 1


# --- SQL-спаны через внутренний `EngineTracer` --------------------------------------

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
    llm_backend="stub",
)


def _broken(error: type[Exception]) -> Any:
    def engine_tracer(*args: object, **kwargs: object) -> None:
        raise error("EngineTracer сменил сигнатуру")

    return engine_tracer


@pytest.fixture(params=[ImportError, TypeError, AttributeError])
def broken_engine_tracer(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> type[Exception]:
    """`EngineTracer` несовместим: класс переехал (`ImportError`) или сменил интерфейс."""
    error: type[Exception] = request.param
    if error is ImportError:
        # `None` в `sys.modules` — так `import` падает `ImportError`.
        monkeypatch.setitem(sys.modules, "opentelemetry.instrumentation.sqlalchemy.engine", None)
    else:
        monkeypatch.setattr(sqlalchemy_engine, "EngineTracer", _broken(error))
    monkeypatch.setattr(
        logging.getLogger("app.infrastructure.telemetry"), "disabled", False
    )
    return error


def _engine_warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if "EngineTracer" in r.getMessage()]


def test_an_incompatible_engine_tracer_does_not_stop_the_api(
    broken_engine_tracer: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    create_app(SETTINGS)

    [record] = _engine_warnings(caplog)
    assert record.levelname == "WARNING"


def test_an_incompatible_engine_tracer_does_not_stop_the_worker(
    broken_engine_tracer: type[Exception],
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[bool] = []

    class _Worker:
        def run(self, handler: object, **kwargs: object) -> None:
            started.append(True)

    monkeypatch.setattr(worker_factory, "build_worker", lambda settings: _Worker())

    worker_factory.run_worker(SETTINGS)

    assert started == [True]
    [record] = _engine_warnings(caplog)
    assert record.levelname == "WARNING"


def test_the_installed_engine_tracer_still_produces_sql_spans(
    spans: InMemorySpanExporter,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Сторож обновления пакета: фоллбэк `instrument_engine` молча выключает спаны SQL,
    # и без этой проверки переезд `EngineTracer` заметили бы только по пустым трейсам.
    monkeypatch.setattr(
        logging.getLogger("app.infrastructure.telemetry"), "disabled", False
    )
    engine = create_engine("sqlite://")
    telemetry.instrument_engine(engine)

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    assert not _engine_warnings(caplog), (
        "EngineTracer из opentelemetry-instrumentation-sqlalchemy несовместим — "
        "спаны SQL пропали; см. TODO в telemetry.instrument_engine"
    )
    assert [s for s in spans.get_finished_spans() if s.kind is SpanKind.CLIENT], (
        "после instrument_engine запрос к базе не дал спана — спаны SQL пропали"
    )


def test_a_failed_query_does_not_put_its_parameters_into_the_process_span(
    spans: InMemorySpanExporter, tmp_path: Any
) -> None:
    # Параметр запроса — код из диффа; ошибка запроса выходит из обработки сообщения.
    # Файловый sqlite: у контейнера пул задан явно (`pool_size`/`max_overflow`),
    # а sqlite в памяти берёт SingletonThreadPool, который их не принимает.
    secret = "def leaked_from_diff(): ..."
    url = f"sqlite:///{tmp_path / 'query.db'}"
    engine = build_container(SETTINGS.model_copy(update={"database_url": url})).engine
    with (
        engine.connect() as connection,
        pytest.raises(OperationalError),
        consuming_span(None, delivery_tag=1),
    ):
        connection.execute(text("SELECT * FROM no_such_table WHERE x = :x"), {"x": secret})

    (process,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.CONSUMER]
    assert process.status.status_code is StatusCode.ERROR
    recorded = [process.status.description or ""] + [
        str(value) for event in process.events for value in (event.attributes or {}).values()
    ]
    assert not [value for value in recorded if secret in value]


# --- метрики пула соединений ---------------------------------------------------------

CONNECTION_COUNT = "db.client.connection.count"
CONNECTION_MAX = "db.client.connection.max"
POOL_ATTRIBUTE = "db.client.connection.pool.name"
STATE_ATTRIBUTE = "db.client.connection.state"


def _pool_snapshot(reader: InMemoryMetricReader, state: str) -> float:
    """Наблюдение снимка пула по состоянию (`used`/`idle`)."""
    key = (
        CONNECTION_COUNT,
        tuple(sorted({POOL_ATTRIBUTE: "app", STATE_ATTRIBUTE: state}.items())),
    )
    return metric_snapshot(reader)[key][0]


def _pool_limit(reader: InMemoryMetricReader) -> float:
    """Наблюдение предела пула."""
    return metric_snapshot(reader)[(CONNECTION_MAX, ((POOL_ATTRIBUTE, "app"),))][0]


def _exported_metric_names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    if data is None:
        return set()
    return {
        metric.name
        for resource_metrics in data.resource_metrics or ()
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


@pytest.fixture
def views_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, InMemoryMetricReader]:
    """Провайдер метрик с нашими View и ридером в память.

    Глобальный провайдер сессии (tests/conftest.py) стоит без View, а
    конфигурация агрегации — Drop старого счётчика и бакеты гистограмм —
    живёт в `build_meter_provider`: проверять её можно только на собранном
    здесь провайдере, как это делает `configure_telemetry` в проде.
    """
    reader = InMemoryMetricReader()
    monkeypatch.setattr(telemetry, "PeriodicExportingMetricReader", lambda exporter: reader)
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "otlp")
    return telemetry.build_meter_provider("dmc-268-api"), reader


@pytest.fixture
def pool_reader(
    views_provider: tuple[Any, InMemoryMetricReader], monkeypatch: pytest.MonkeyPatch
) -> InMemoryMetricReader:
    """Тот же провайдер, с метрами телеметрии (`_meter`), переведёнными на него."""
    provider, reader = views_provider
    monkeypatch.setattr(telemetry, "_meter", provider.get_meter("telemetry-test"))
    return reader


def test_the_pool_snapshot_matches_the_real_pool(
    pool_reader: InMemoryMetricReader, tmp_path: Any
) -> None:
    """used/idle — фактическое состояние пула, max — pool_size + max_overflow."""
    engine = create_engine(f"sqlite:///{tmp_path / 'pool.db'}", pool_size=2, max_overflow=3)
    telemetry.instrument_pool(engine, 2, 3)

    with engine.connect(), engine.connect():
        assert _pool_snapshot(pool_reader, "used") == 2
        assert _pool_snapshot(pool_reader, "idle") == 0
    # Соединения вернулись в пул — снимок это видит, хотя событий никто не звал.
    assert _pool_snapshot(pool_reader, "used") == 0
    assert _pool_snapshot(pool_reader, "idle") == 2
    assert _pool_limit(pool_reader) == 5


def test_pool_instruments_are_counted_in_connections(
    pool_reader: InMemoryMetricReader, tmp_path: Any
) -> None:
    """Unit `{connection}` — у обоих приборов пула."""
    engine = create_engine(f"sqlite:///{tmp_path / 'unit.db'}", pool_size=1, max_overflow=1)
    telemetry.instrument_pool(engine, 1, 1)

    data = pool_reader.get_metrics_data()
    assert data is not None
    units = {
        metric.name: metric.unit
        for resource_metrics in data.resource_metrics or ()
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }
    assert units[CONNECTION_COUNT] == "{connection}"
    assert units[CONNECTION_MAX] == "{connection}"


def test_a_non_queuepool_engine_gets_no_pool_metrics(
    pool_reader: InMemoryMetricReader,
) -> None:
    """Не-`QueuePool` (как `StaticPool` тестов) — тихий выход: приборов нет."""
    engine = create_engine("sqlite://", poolclass=StaticPool)

    telemetry.instrument_pool(engine, 5, 10)

    assert _exported_metric_names(pool_reader).isdisjoint({CONNECTION_COUNT, CONNECTION_MAX})


def test_a_connection_used_inside_untraced_does_not_drift_the_snapshot(
    pool_reader: InMemoryMetricReader, tmp_path: Any
) -> None:
    """Снимок — состояние пула, а не события: `untraced()` его не ломает.

    Событийный счётчик contrib пропустил бы checkin внутри подавления и
    остался бы с «использованным» соединением, которого нет; снимок в минус
    не уходит в принципе — он читает `checkedout`/`checkedin` на момент сбора.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'untraced.db'}", pool_size=1, max_overflow=1)
    telemetry.instrument_pool(engine, 1, 1)

    with telemetry.untraced(), engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        assert _pool_snapshot(pool_reader, "used") == 1
        assert _pool_snapshot(pool_reader, "idle") == 0

    assert _pool_snapshot(pool_reader, "used") == 0
    assert _pool_snapshot(pool_reader, "idle") == 1


def test_the_deprecated_event_counter_is_not_exported_and_sql_spans_live(
    pool_reader: InMemoryMetricReader, tmp_path: Any, spans: InMemorySpanExporter
) -> None:
    """Старый счётчик contrib создаётся, но гасится View-агрегацией; спаны SQL живы."""
    engine = create_engine(f"sqlite:///{tmp_path / 'drop.db'}")
    telemetry.instrument_engine(engine)
    telemetry.instrument_pool(engine, 5, 10)

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    names = _exported_metric_names(pool_reader)
    assert "db.client.connections.usage" not in names
    assert {CONNECTION_COUNT, CONNECTION_MAX} <= names
    assert [s for s in spans.get_finished_spans() if s.kind is SpanKind.CLIENT], (
        "после переноса счётчика в снимок пула спаны SQL пропали"
    )


def test_the_container_engine_pool_is_sized_from_the_settings(
    views_provider: tuple[Any, InMemoryMetricReader],
    pool_reader: InMemoryMetricReader,
) -> None:
    """Пул создаётся с явными настройками; дефолты 5/10 — прежнее поведение."""
    container = build_container(SETTINGS)
    pool = container.engine.pool
    assert isinstance(pool, QueuePool)
    assert pool.size() == 5

    tuned = build_container(
        SETTINGS.model_copy(update={"db_pool_size": 2, "db_max_overflow": 1})
    )
    tuned_pool = tuned.engine.pool
    assert isinstance(tuned_pool, QueuePool)
    assert tuned_pool.size() == 2
    # Предел, который публикует `instrument_pool`, совпадает с фактической
    # конфигурацией движка: метрика обязана не расходиться с пулом.
    telemetry.instrument_pool(tuned.engine, 2, 1)
    assert _pool_limit(pool_reader) == 3


# --- бакеты гистограмм длительности --------------------------------------------------

# Бакеты задаёт View в `build_meter_provider`: этапы прогона и вызовы модели
# длятся от десятых доли до минут, дефолтные миллисекундные бакеты бесполезны.
DURATION_BUCKETS = (
    0.1,
    0.25,
    0.5,
    1,
    2.5,
    5,
    10,
    30,
    60,
    120,
    300,
)


def test_duration_histograms_get_explicit_second_buckets(
    views_provider: tuple[Any, InMemoryMetricReader],
) -> None:
    """Обе гистограммы длительности — с явными бакетами в секундах.

    0.4 с обязан попасть в бакет (0.25, 0.5]: на дефолтных бакетах (до
    10–50 мс) почти вся длительность прогона лежала в первом бакете, и
    `histogram_quantile` по такой гистограмме не считается.
    """
    provider, reader = views_provider
    meter = provider.get_meter("buckets-test")
    stage = meter.create_histogram("review_stage_duration", unit="s")
    stage.record(0.4, {"stage": "analysing"})
    call = meter.create_histogram("review_llm_call_duration", unit="s")
    call.record(0.4, {"task": "review", "model": "m"})

    for name, attributes in (
        ("review_stage_duration", {"stage": "analysing"}),
        ("review_llm_call_duration", {"task": "review", "model": "m"}),
    ):
        count, bounds, buckets = metric_histogram(reader, name, **attributes)
        assert count == 1
        assert bounds == DURATION_BUCKETS
        # 0.4 с — третий бакет (0.25, 0.5]; в остальных пусто.
        assert buckets == tuple(1 if index == 2 else 0 for index in range(len(bounds) + 1))


# --- остановка процесса --------------------------------------------------------------


class _RecordingProcessor(SpanProcessor):
    def __init__(self) -> None:
        self.shut_down = False

    def shutdown(self) -> None:
        self.shut_down = True


def test_shutdown_closes_only_the_providers_configure_telemetry_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    own = TracerProvider(shutdown_on_exit=False)
    processor = _RecordingProcessor()
    own.add_span_processor(processor)
    monkeypatch.setattr(telemetry, "_installed", own)

    telemetry.shutdown_tracing()

    assert processor.shut_down


def test_shutdown_leaves_a_foreign_provider_alone(
    monkeypatch: pytest.MonkeyPatch, spans: InMemorySpanExporter
) -> None:
    # Глобальный провайдер поставил `tests/conftest.py`, а не `configure_telemetry`.
    monkeypatch.setattr(telemetry, "_installed", None)

    telemetry.shutdown_tracing()

    trace.get_tracer("test").start_span("after-shutdown").end()
    assert [s.name for s in spans.get_finished_spans()] == ["after-shutdown"]


class _OtlpSpanExporter(InMemorySpanExporter):
    def __init__(self, endpoint: str) -> None:
        super().__init__()


@pytest.fixture
def otlp_exporter_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Экспортёр OTLP «установлен»: без него автонастройка FastAPI только пишет
    предупреждение и ничего не добавляет — тест не отличил бы её от выключенной."""
    prefix = "opentelemetry.exporter.otlp.proto.http"
    for module, name in [
        ("trace_exporter", "OTLPSpanExporter"),
        ("_log_exporter", "OTLPLogExporter"),
        ("metric_exporter", "OTLPMetricExporter"),
    ]:
        stub = types.ModuleType(f"{prefix}.{module}")
        setattr(stub, name, _OtlpSpanExporter)
        monkeypatch.setitem(sys.modules, f"{prefix}.{module}", stub)
    # Метрики и логи FastAPI не настраиваем — проверяется только экспорт спанов.
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setenv("OTEL_LOGS_EXPORTER", "none")


def test_fastapi_does_not_add_exporters_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, otlp_exporter_installed: None
) -> None:
    # Даже с явной просьбой окружения: `telemetry=` в `create_app` её перекрывает.
    monkeypatch.setenv("FASTAPI_OTEL_AUTO_CONFIGURE", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4318")
    provider = trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    app = create_app(SETTINGS)
    before = provider._active_span_processor._span_processors

    with TestClient(app) as client:
        client.get("/health")

    assert provider._active_span_processor._span_processors == before


def test_the_api_closes_tracing_when_the_app_shuts_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[bool] = []
    monkeypatch.setattr(telemetry, "shutdown_tracing", lambda: closed.append(True))

    with TestClient(create_app(SETTINGS)):
        assert closed == []

    assert closed == [True]


# --- стабильные имена semconv ----------------------------------------------------------


@pytest.fixture
def fresh_semconv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Пакеты contrib ещё не читали `OTEL_SEMCONV_STABILITY_OPT_IN` — как в новом процессе."""
    monkeypatch.delenv("OTEL_SEMCONV_STABILITY_OPT_IN", raising=False)
    monkeypatch.setattr(_OpenTelemetrySemanticConventionStability, "_initialized", False)
    monkeypatch.setattr(
        _OpenTelemetrySemanticConventionStability, "_OTEL_SEMCONV_STABILITY_SIGNAL_MAPPING", {}
    )


def test_an_explicit_semconv_opt_in_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OTEL_SEMCONV_STABILITY_OPT_IN", "database/dup")

    telemetry.opt_in_stable_semconv()

    assert os.environ["OTEL_SEMCONV_STABILITY_OPT_IN"] == "database/dup"


def test_sql_spans_carry_only_the_stable_attribute_names(
    fresh_semconv: None, spans: InMemorySpanExporter
) -> None:
    engine = create_engine("sqlite://")
    telemetry.instrument_engine(engine)

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))

    (query,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.CLIENT]
    attributes = query.attributes or {}
    assert attributes["db.query.text"] == "SELECT 1"
    assert attributes["db.system.name"] == "sqlite"
    assert "db.statement" not in attributes
    assert "db.system" not in attributes


class _Ok(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None: ...


@pytest.fixture
def http_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Ok)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()


def test_httpx_spans_carry_the_stable_attribute_names(
    fresh_semconv: None, spans: InMemorySpanExporter, http_server: str
) -> None:
    # Режим httpx-инструментор запоминает при `instrument()` — переинструментируем.
    HTTPXClientInstrumentor().uninstrument()
    telemetry.configure_telemetry(telemetry.API_SERVICE)

    httpx.get(f"{http_server}/ping")

    (request,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.CLIENT]
    attributes = request.attributes or {}
    assert attributes["http.request.method"] == "GET"
    assert attributes["url.full"] == f"{http_server}/ping"
    assert attributes["http.response.status_code"] == 200
    assert "http.method" not in attributes
    assert "http.url" not in attributes


class _Recording(BaseHTTPRequestHandler):
    """Запоминает заголовки запросов на сервере `server.received`."""

    def do_GET(self) -> None:
        self.server.received.append(dict(self.headers))  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None: ...


def test_an_outgoing_request_leaves_without_traceparent(
    spans: InMemorySpanExporter,
) -> None:
    """Запрос httpx при активном спане уходит без нашего контекста — и при этом в трейсе."""
    telemetry.configure_telemetry(telemetry.WORKER_SERVICE)
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recording)
    server.received = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with trace.get_tracer(__name__).start_as_current_span("review.analysing") as parent:
            httpx.get(f"http://127.0.0.1:{server.server_port}/ping")
    finally:
        server.shutdown()
        server.server_close()

    (headers,) = server.received  # type: ignore[attr-defined]
    assert not {"traceparent", "tracestate"} & {name.lower() for name in headers}
    (request,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.CLIENT]
    assert request.parent is not None
    assert request.parent.span_id == parent.get_span_context().span_id


def test_a_pipeline_failing_on_a_stage_closes_its_otel_span_as_an_error(
    spans: InMemorySpanExporter,
) -> None:
    """Настоящий `OtelStageTracer`: упавший этап — ERROR, после выхода контекст чистый."""
    uow = FakeUnitOfWork()
    _seed_run(uow)

    with pytest.raises(LlmTimeoutError):
        _run(
            uow,
            ScriptedLlmGateway([LlmTimeoutError("таймаут", tokens_used=30)]),
            stage_tracer=OtelStageTracer(),
        )

    stages = [s for s in spans.get_finished_spans() if s.name.startswith("review.")]
    assert [s.name for s in stages] == [
        "review.fetching_diff",
        "review.building_context",
        "review.analysing",
    ]
    assert [s.status.status_code for s in stages] == [
        StatusCode.UNSET,
        StatusCode.UNSET,
        StatusCode.ERROR,
    ]
    assert (stages[-1].attributes or {})["error.type"] == "LLM_TIMEOUT"
    assert not trace.get_current_span().get_span_context().is_valid
