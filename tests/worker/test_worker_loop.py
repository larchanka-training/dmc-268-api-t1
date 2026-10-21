"""Consume-цикл воркера на фейковом канале: выметание, спан обработки, параметры соединения.

Без брокера.
"""

import json
from types import SimpleNamespace
from typing import Any

import pika
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.config import Settings
from app.infrastructure.queue.rabbitmq import to_wire_message
from app.worker import factory
from app.worker.factory import Worker, build_review_handler

from ..fakes.unit_of_work import FakeUnitOfWork
from .test_review_handler import JOB, LIMITS, LLM_LIMITS, TEXTS, _Deps


class FakeChannel:
    def __init__(self, deliveries: list[tuple[Any, Any, Any]]) -> None:
        self._deliveries = deliveries
        self.acked: list[int] = []

    def consume(self, queue: str, inactivity_timeout: float):
        yield from self._deliveries

    def basic_ack(self, tag: int) -> None:
        self.acked.append(tag)

    def basic_nack(self, tag: int, requeue: bool) -> None:
        raise AssertionError("не ожидался nack")

    def cancel(self) -> None: ...


class RecordingChannel(FakeChannel):
    """Канал, который принимает и nack — сообщение ушло бы в DLQ."""

    def __init__(self, deliveries: list[tuple[Any, Any, Any]]) -> None:
        super().__init__(deliveries)
        self.nacked: list[int] = []

    def basic_nack(self, tag: int, requeue: bool) -> None:
        self.nacked.append(tag)


class FakeConnection:
    def close(self) -> None: ...


def _worker(channel: FakeChannel) -> Worker:
    return Worker(connection=FakeConnection(), channel=channel)  # type: ignore[arg-type]


def test_sweep_runs_on_idle_ticks_and_a_failing_sweep_does_not_stop_consumption() -> None:
    message = (SimpleNamespace(delivery_tag=1), None, b"{}")
    channel = FakeChannel([(None, None, None), message])
    calls: list[int] = []

    def sweep() -> None:
        calls.append(1)
        raise RuntimeError("база недоступна")

    _worker(channel).run(lambda body: None, limit=1, sweep=sweep, sweep_interval=0)

    assert calls  # проход был
    assert channel.acked == [1]  # и обработка сообщения продолжилась


def test_sweep_is_throttled_by_the_interval() -> None:
    ticks = [(None, None, None)] * 3 + [(SimpleNamespace(delivery_tag=1), None, b"{}")]
    channel = FakeChannel(ticks)
    calls: list[int] = []

    _worker(channel).run(
        lambda body: None, limit=1, sweep=lambda: calls.append(1), sweep_interval=3600
    )

    assert calls == [1]  # первый проход сразу, остальные — не раньше интервала


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"{not json", id="битое сообщение"),
        # Прогона в базе нет — `run_review` падает `LookupError`.
        pytest.param(json.dumps(to_wire_message(JOB)).encode("utf-8"), id="нет прогона"),
    ],
)
def test_a_failed_message_closes_its_span_with_the_error(
    spans: InMemorySpanExporter, body: bytes
) -> None:
    channel = RecordingChannel([(SimpleNamespace(delivery_tag=1), None, body)])
    handler = build_review_handler(_Deps(FakeUnitOfWork()), LIMITS, LLM_LIMITS, TEXTS)

    _worker(channel).run(handler, limit=1)

    assert channel.nacked == [1]
    (process,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.CONSUMER]
    assert process.status.status_code is StatusCode.ERROR
    assert [event.name for event in process.events] == ["exception"]


def test_a_processed_message_closes_its_span_ok(spans: InMemorySpanExporter) -> None:
    channel = FakeChannel([(SimpleNamespace(delivery_tag=1), None, b"{}")])

    _worker(channel).run(lambda body: None, limit=1)

    (process,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.CONSUMER]
    assert process.status.status_code is StatusCode.UNSET
    assert not process.events


def test_worker_connection_has_heartbeat_disabled(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    class FakeBlockingConnection:
        def __init__(self, parameters: pika.URLParameters) -> None:
            captured["heartbeat"] = parameters.heartbeat

        def channel(self) -> Any:
            return SimpleNamespace(
                exchange_declare=lambda **kw: None,
                queue_declare=lambda **kw: None,
                queue_bind=lambda **kw: None,
                basic_qos=lambda **kw: None,
            )

    monkeypatch.setattr(pika, "BlockingConnection", FakeBlockingConnection)

    factory.build_worker(
        Settings(
            database_url="postgresql+psycopg://t:t@localhost/t",
            rabbitmq_url="amqp://guest:guest@localhost//",
            github_webhook_secret="s",
        )
    )

    assert captured["heartbeat"] == 0
