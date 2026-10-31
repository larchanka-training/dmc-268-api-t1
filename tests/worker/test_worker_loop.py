"""Consume-цикл воркера на фейковом канале: выметание, спан обработки, остановка по сигналу,
параметры соединения.

Без брокера.
"""

import json
import logging
import signal
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import pika
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode

from app.config import Settings
from app.domain.llm_errors import LlmTimeoutError
from app.infrastructure.queue.rabbitmq import CONSUMER_ARGUMENTS, to_wire_message
from app.infrastructure.telemetry import OtelStageTracer
from app.worker import factory
from app.worker.factory import StopRequest, Worker, build_review_handler

from ..application.test_review_pipeline import _run, _seed_run
from ..fakes.llm_gateway import ScriptedLlmGateway
from ..fakes.queue import FakeChannel, FakeConnection
from ..fakes.unit_of_work import FakeUnitOfWork
from .test_review_handler import JOB, LIMITS, LLM_LIMITS, TEXTS, _Deps


def _strict(deliveries: list[tuple[Any, Any, Any]]) -> FakeChannel:
    """Канал, в котором nack роняет тест: сообщение не должно уйти в DLQ."""
    return FakeChannel(deliveries, allow_nack=False)


def _worker(channel: FakeChannel, connection: FakeConnection | None = None) -> Worker:
    return Worker(connection=connection or FakeConnection(), channel=channel)  # type: ignore[arg-type]


def test_sweep_runs_on_idle_ticks_and_a_failing_sweep_does_not_stop_consumption() -> None:
    message = (SimpleNamespace(delivery_tag=1, redelivered=False), None, b"{}")
    channel = _strict([(None, None, None), message])
    calls: list[int] = []

    def sweep() -> None:
        calls.append(1)
        raise RuntimeError("база недоступна")

    _worker(channel).run(lambda body: None, limit=1, sweep=sweep, sweep_interval=0)

    assert calls  # проход был
    assert channel.acked == [1]  # и обработка сообщения продолжилась


def test_sweep_is_throttled_by_the_interval() -> None:
    ticks = [(None, None, None)] * 3 + [(SimpleNamespace(delivery_tag=1, redelivered=False), None, b"{}")]
    channel = _strict(ticks)
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
    channel = FakeChannel([(SimpleNamespace(delivery_tag=1, redelivered=False), None, body)])
    handler = build_review_handler(_Deps(FakeUnitOfWork()), LIMITS, LLM_LIMITS, TEXTS)

    _worker(channel).run(handler, limit=1)

    assert channel.nacked == [1]
    (process,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.CONSUMER]
    assert process.status.status_code is StatusCode.ERROR
    assert [event.name for event in process.events] == ["exception"]
    assert (process.attributes or {}).get("review_run_id") == review_run_id


def test_a_processed_message_closes_its_span_ok(spans: InMemorySpanExporter) -> None:
    channel = _strict([(SimpleNamespace(delivery_tag=1, redelivered=False), None, b"{}")])

    _worker(channel).run(lambda body: None, limit=1)

    (process,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.CONSUMER]
    assert process.status.status_code is StatusCode.UNSET
    assert not process.events


def test_a_stage_of_a_failed_message_does_not_leak_into_the_next_spans(
    spans: InMemorySpanExporter,
) -> None:
    # Первое сообщение падает посреди этапа `analysing`; проход очистки и обработка
    # следующего сообщения обязаны начать свои трейсы, а не стать детьми упавшего этапа.
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateways = iter([ScriptedLlmGateway([LlmTimeoutError("таймаут", tokens_used=1)])])

    def handler(body: bytes) -> None:
        if body == b"fail":
            _run(uow, next(gateways), stage_tracer=OtelStageTracer())

    channel = FakeChannel(
        [
            (SimpleNamespace(delivery_tag=1, redelivered=False), None, b"fail"),
            (None, None, None),
            (SimpleNamespace(delivery_tag=2, redelivered=False), None, b"ok"),
        ]
    )

    _worker(channel).run(handler, limit=2, sweep=lambda: None, sweep_interval=0)

    assert channel.nacked == [1] and channel.acked == [2]
    finished = spans.get_finished_spans()
    failed, processed = [s for s in finished if s.kind is SpanKind.CONSUMER]
    (stage,) = [s for s in finished if s.name == "review.analysing"]
    assert stage.status.status_code is StatusCode.ERROR
    assert failed.status.status_code is StatusCode.ERROR
    after_failure = [s for s in finished if s.name == "review.sweep"][1:] + [processed]
    assert after_failure and all(s.parent is None for s in after_failure)


# --- остановка по сигналу ------------------------------------------------------------


def test_a_stop_request_lets_the_current_message_finish_and_takes_no_more() -> None:
    first, second = [(SimpleNamespace(delivery_tag=tag, redelivered=False), None, b"{}") for tag in (1, 2)]
    channel = _strict([first, second])
    connection = FakeConnection()
    stop = StopRequest()

    def handler(body: bytes) -> None:
        stop.requested = True  # сигнал пришёл посреди обработки

    _worker(channel, connection).run(handler, stop=stop)

    # Потребитель снят до подтверждения: пока сообщение держит prefetch=1, брокер
    # второе не выдаст, и возвращать его `basic.reject` с тратой попытки не придётся.
    assert channel.events == ["cancel", "ack 1"]
    assert connection.closed


def test_a_message_delivered_after_a_stop_request_is_not_taken() -> None:
    # Сигнал пришёл на простое или в окно ожидания, и тут же доставлено сообщение.
    channel = _strict([(SimpleNamespace(delivery_tag=1, redelivered=False), None, b"{}")])
    connection = FakeConnection()
    handled: list[bytes] = []

    _worker(channel, connection).run(handled.append, stop=StopRequest(requested=True))

    assert handled == []
    # Сначала снять потребителя, потом отпустить: `nack` раньше освободил бы prefetch.
    assert channel.events == ["cancel", "requeue 1"]
    assert connection.closed


def test_a_message_after_a_stop_during_the_sweep_is_not_taken() -> None:
    channel = _strict([(SimpleNamespace(delivery_tag=1, redelivered=False), None, b"{}")])
    stop = StopRequest()
    handled: list[bytes] = []

    def sweep() -> None:
        stop.requested = True  # сигнал пришёл посреди прохода очистки

    _worker(channel).run(handled.append, sweep=sweep, sweep_interval=0, stop=stop)

    assert handled == []
    assert channel.events == ["cancel", "requeue 1"]


class IdleChannel(FakeChannel):
    """Пустая очередь: только такты простоя, бесконечно."""

    def __init__(self) -> None:
        super().__init__([], allow_nack=False)
        self.idle_ticks = 0

    def consume(
        self, queue: str, inactivity_timeout: float, arguments: dict[str, Any] | None = None
    ) -> Iterator[Any]:
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

    settings = Settings(
        database_url="postgresql+psycopg://t:t@localhost/t",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="s",
        llm_backend="stub",
    )
    # Настройки — загрузчиком: `run_worker` читает их сам после настройки логов.
    factory.run_worker(lambda: settings)

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


def test_a_limited_run_takes_the_consumer_off_before_the_last_ack() -> None:
    channel = FakeChannel([(SimpleNamespace(delivery_tag=tag, redelivered=False), None, b"{}") for tag in (1, 2)])

    _worker(channel).run(lambda body: None, limit=1)

    assert channel.events == ["cancel", "ack 1"]


def test_a_consumer_cancelled_by_the_broker_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    """`x-consumer-timeout`: брокер снял потребителя, `consume` кончился сам. Воркер
    выходит не молча и второй раз потребителя не снимает."""
    channel = FakeChannel([(None, None, None)])

    with caplog.at_level(logging.WARNING, logger="app.worker.factory"):
        _worker(channel).run(lambda body: None)

    assert channel.events == []
    assert channel.consumer_arguments == CONSUMER_ARGUMENTS
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "отменил потребителя" in record.getMessage()
