"""Спан обработки при повторной доставке. Нужен живой RabbitMQ.

Первая попытка умирает без подтверждения (как воркер, убитый посреди
обработки): соединение закрывается с неподтверждённым сообщением, и брокер
возвращает его в очередь. Второй воркер получает ту же публикацию с
`basic.deliver.redelivered = true` — спан обработки обязан это показать
(спека `observability`, сценарий «Повторная доставка сообщения»).
"""

from collections.abc import Iterator
from uuid import UUID

import pika
import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind, format_trace_id

from app.config import Settings
from app.domain.entities import ReviewJob
from app.infrastructure.queue.rabbitmq import (
    DLQ_NAME,
    QUEUE_NAME,
    RabbitMQJobQueue,
    declare_topology,
)
from app.worker.factory import build_worker

from ..conftest import TEST_RABBITMQ_URL, requires_broker

pytestmark = [pytest.mark.integration, requires_broker]

JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    event_type="pull_request",
    action="opened",
    installation_id=512804923,
    repository_provider_id="987654",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="abc123def",
    base_sha="fed654cba",
)


@pytest.fixture
def channel() -> Iterator[pika.adapters.blocking_connection.BlockingChannel]:
    assert TEST_RABBITMQ_URL is not None
    connection = pika.BlockingConnection(pika.URLParameters(TEST_RABBITMQ_URL))
    ch = connection.channel()
    declare_topology(ch)
    ch.queue_purge(QUEUE_NAME)
    ch.queue_purge(DLQ_NAME)
    try:
        yield ch
    finally:
        ch.queue_purge(QUEUE_NAME)
        ch.queue_purge(DLQ_NAME)
        connection.close()


def _settings() -> Settings:
    assert TEST_RABBITMQ_URL is not None
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url=TEST_RABBITMQ_URL,
        github_webhook_secret="test-secret",
    )


def _die_without_ack(body: bytes) -> None:
    """Воркер умер посреди обработки: сообщение не подтверждено."""
    raise KeyboardInterrupt


def test_a_redelivered_message_gets_a_process_span_with_redelivered_true(
    channel: pika.adapters.blocking_connection.BlockingChannel,
    spans: InMemorySpanExporter,
) -> None:
    assert TEST_RABBITMQ_URL is not None
    RabbitMQJobQueue(TEST_RABBITMQ_URL).enqueue(JOB)

    # Первая попытка: смерть без ack — `except Exception` в цикле её не ловит,
    # соединение закрывается в `finally`, и брокер возвращает сообщение в очередь.
    with pytest.raises(KeyboardInterrupt):
        build_worker(_settings()).run(_die_without_ack)

    # Вторая попытка забирает ту же публикацию — уже с признаком повторной доставки.
    handled: list[bytes] = []
    build_worker(_settings()).run(handled.append, limit=1)

    assert handled  # сообщение действительно обработано заново
    publish, first, second = [
        span
        for span in spans.get_finished_spans()
        if span.kind in (SpanKind.PRODUCER, SpanKind.CONSUMER)
    ]

    def _trace_id(span) -> str:
        context = span.context or span.get_span_context()
        assert context is not None
        return format_trace_id(context.trace_id)

    # Обе попытки — продолжения того же publish: повтор в трейсе отличим
    # только признаком redelivered, а не отдельным трейсом.
    assert _trace_id(first) == _trace_id(publish)
    assert _trace_id(second) == _trace_id(publish)
    assert first.attributes is not None
    assert second.attributes is not None
    assert first.attributes["messaging.rabbitmq.message.redelivered"] is False
    assert second.attributes["messaging.rabbitmq.message.redelivered"] is True
