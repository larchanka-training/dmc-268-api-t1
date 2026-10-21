"""Consume-цикл воркера на фейковом канале: выметание, спан обработки, остановка по сигналу,
параметры соединения.

Без брокера.
"""

import json
import signal
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pika
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.config import Settings
from app.infrastructure.queue.rabbitmq import to_wire_message
from app.worker import factory
from app.worker.factory import StopRequest, Worker, build_review_handler

from ..fakes.unit_of_work import FakeUnitOfWork
from .test_review_handler import JOB, LIMITS, LLM_LIMITS, TEXTS, _Deps


class FakeChannel:
    def __init__(self, deliveries: list[tuple[Any, Any, Any]]) -> None:
        self._deliveries = deliveries
        self.acked: list[int] = []
        self.cancelled = False

    def consume(self, queue: str, inactivity_timeout: float):
        yield from self._deliveries

    def basic_ack(self, tag: int) -> None:
        self.acked.append(tag)

    def basic_nack(self, tag: int, requeue: bool) -> None:
        raise AssertionError("не ожидался nack")

    def cancel(self) -> None:
        self.cancelled = True


class RecordingChannel(FakeChannel):
    """Канал, который принимает и nack — сообщение ушло бы в DLQ."""

    def __init__(self, deliveries: list[tuple[Any, Any, Any]]) -> None:
        super().__init__(deliveries)
        self.nacked: list[int] = []

    def basic_nack(self, tag: int, requeue: bool) -> None:
        self.nacked.append(tag)


class FakeConnection:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _worker(channel: FakeChannel, connection: FakeConnection | None = None) -> Worker:
    return Worker(connection=connection or FakeConnection(), channel=channel)  # type: ignore[arg-type]


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
    ("body", "review_run_id"),
    [
        # Сообщение не разобрано — прогон неизвестен, атрибута нет.
        pytest.param(b"{not json", None, id="битое сообщение"),
        # Прогона в базе нет — `run_review` падает `LookupError`; id из сообщения известен.
        pytest.param(
            json.dumps(to_wire_message(JOB)).encode("utf-8"),
            str(JOB.review_run_id),
            id="нет прогона",
        ),
    ],
)
def test_a_failed_message_closes_its_span_with_the_error(
    spans: InMemorySpanExporter, body: bytes, review_run_id: str | None
) -> None:
    channel = RecordingChannel([(SimpleNamespace(delivery_tag=1), None, body)])
    handler = build_review_handler(_Deps(FakeUnitOfWork()), LIMITS, LLM_LIMITS, TEXTS)

    _worker(channel).run(handler, limit=1)

    assert channel.nacked == [1]
    (process,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.CONSUMER]
    assert process.status.status_code is StatusCode.ERROR
    assert [event.name for event in process.events] == ["exception"]
    assert (process.attributes or {}).get("review_run_id") == review_run_id


def test_a_processed_message_closes_its_span_ok(spans: InMemorySpanExporter) -> None:
    channel = FakeChannel([(SimpleNamespace(delivery_tag=1), None, b"{}")])

    _worker(channel).run(lambda body: None, limit=1)

    (process,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.CONSUMER]
    assert process.status.status_code is StatusCode.UNSET
    assert not process.events


# --- остановка по сигналу ------------------------------------------------------------


def test_a_stop_request_lets_the_current_message_finish_and_takes_no_more() -> None:
    first, second = [(SimpleNamespace(delivery_tag=tag), None, b"{}") for tag in (1, 2)]
    channel = FakeChannel([first, second])
    connection = FakeConnection()
    stop = StopRequest()

    def handler(body: bytes) -> None:
        stop.requested = True  # сигнал пришёл посреди обработки

    _worker(channel, connection).run(handler, stop=stop)

    assert channel.acked == [1]  # сообщение в работе подтверждено, второе не взято
    assert channel.cancelled and connection.closed


class IdleChannel(FakeChannel):
    """Пустая очередь: только такты простоя, бесконечно."""

    def __init__(self) -> None:
        super().__init__([])
        self.idle_ticks = 0

    def consume(self, queue: str, inactivity_timeout: float) -> Iterator[Any]:
        while True:
            self.idle_ticks += 1
            assert self.idle_ticks < 100, "цикл не вышел по просьбе остановиться"
            yield (None, None, None)


def test_a_stop_request_ends_the_idle_wait_on_the_next_tick() -> None:
    channel = IdleChannel()
    sweeps: list[int] = []

    _worker(channel).run(
        lambda body: None, sweep=lambda: sweeps.append(1), stop=StopRequest(requested=True)
    )

    assert channel.idle_ticks == 1
    assert sweeps == []  # проход очистки после просьбы остановиться не начинается
    assert channel.cancelled


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
def test_a_stop_signal_stops_the_worker_and_flushes_the_traces(
    monkeypatch: pytest.MonkeyPatch, signum: signal.Signals
) -> None:
    before = signal.getsignal(signum)
    seen: dict[str, bool] = {}
    flushed: list[bool] = []

    class _Worker:
        def run(self, handler: object, *, stop: StopRequest, **kwargs: object) -> None:
            signal.raise_signal(signum)  # процесс не умер: обработчик поставил флаг
            seen["requested"] = stop.requested
            # Второй сигнал действовал бы как обычно: обработчик вернул прежний.
            seen["restored"] = signal.getsignal(signum) == before

    monkeypatch.setattr(factory, "build_worker", lambda settings: _Worker())
    monkeypatch.setattr(factory, "shutdown_tracing", lambda: flushed.append(True))

    factory.run_worker(
        Settings(
            database_url="postgresql+psycopg://t:t@localhost/t",
            rabbitmq_url="amqp://guest:guest@localhost//",
            github_webhook_secret="s",
            llm_backend="stub",
        )
    )

    assert seen == {"requested": True, "restored": True}
    assert flushed == [True]  # провайдер трейсов закрыт после выхода цикла
    assert signal.getsignal(signum) == before


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
