"""Интеграционный тест consume-цикла воркера. Нужен живой RabbitMQ.

Обработчик здесь — заглушка (записывает вызов); подключение доменного
пайплайна как реального обработчика — предмет `add-review-pipeline`.
"""

import json
import time
from collections.abc import Iterator
from uuid import UUID

import pika
import pytest

from app.config import Settings
from app.domain.entities import ReviewJob
from app.infrastructure.queue.rabbitmq import (
    DLQ_NAME,
    QUEUE_NAME,
    RabbitMQJobQueue,
    declare_topology,
)
from app.worker.factory import StopRequest, build_worker

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


@pytest.fixture
def settings() -> Settings:
    assert TEST_RABBITMQ_URL is not None
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url=TEST_RABBITMQ_URL,
        github_webhook_secret="test-secret",
    )


def test_worker_acks_after_a_successful_handler(
    channel: pika.adapters.blocking_connection.BlockingChannel, settings: Settings
) -> None:
    RabbitMQJobQueue(TEST_RABBITMQ_URL or "").enqueue(JOB)
    received: list[bytes] = []

    build_worker(settings).run(received.append, limit=1)

    assert len(received) == 1
    assert json.loads(received[0])["job_id"] == str(JOB.id)
    method, _, _ = channel.basic_get(QUEUE_NAME, auto_ack=True)
    assert method is None  # сообщение подтверждено, очередь пуста


def test_worker_nacks_to_dlq_when_the_handler_raises(
    channel: pika.adapters.blocking_connection.BlockingChannel, settings: Settings
) -> None:
    RabbitMQJobQueue(TEST_RABBITMQ_URL or "").enqueue(JOB)

    def failing_handler(body: bytes) -> None:
        raise ValueError("boom")

    build_worker(settings).run(failing_handler, limit=1)

    dlq_method = None
    for _ in range(20):
        dlq_method, _, _ = channel.basic_get(DLQ_NAME, auto_ack=True)
        if dlq_method is not None:
            break
        time.sleep(0.05)
    assert dlq_method is not None


# --- штатная остановка не тратит попытку доставки (#223) ----------------------


class _ProcessDied(BaseException):
    """Смерть процесса посреди обработки: consume-цикл её не ловит."""


def _delivery_count(channel: pika.adapters.blocking_connection.BlockingChannel) -> int:
    """Сколько неудачных доставок quorum-очередь насчитала сообщению (`x-delivery-count`)."""
    method, properties, _ = channel.basic_get(QUEUE_NAME, auto_ack=True)
    assert method is not None, "сообщение не вернулось в очередь"
    assert properties is not None
    return int((properties.headers or {}).get("x-delivery-count", 0))


def test_a_stopping_worker_returns_the_message_without_spending_a_delivery(
    channel: pika.adapters.blocking_connection.BlockingChannel, settings: Settings
) -> None:
    RabbitMQJobQueue(TEST_RABBITMQ_URL or "").enqueue(JOB)
    handled: list[bytes] = []

    build_worker(settings).run(handled.append, stop=StopRequest(requested=True))

    assert handled == []
    assert _delivery_count(channel) == 0


def test_a_stop_during_processing_leaves_the_next_message_unspent(
    channel: pika.adapters.blocking_connection.BlockingChannel, settings: Settings
) -> None:
    """Сигнал пришёл, пока сообщение в руках: оно подтверждается, а следующее брокер
    не выдаёт и попытку на нём не тратит. Держится на порядке `cancel` → `ack` при
    prefetch=1 — проверяем на живом брокере, а не на фейке."""
    queue = RabbitMQJobQueue(TEST_RABBITMQ_URL or "")
    queue.enqueue(JOB)
    queue.enqueue(JOB)
    stop = StopRequest()
    handled: list[bytes] = []

    def handle_and_stop(body: bytes) -> None:
        handled.append(body)
        stop.requested = True

    build_worker(settings).run(handle_and_stop, stop=stop)

    assert len(handled) == 1
    assert _delivery_count(channel) == 0
    assert channel.queue_declare(QUEUE_NAME, passive=True).method.message_count == 0


def test_a_dying_worker_spends_a_delivery(
    channel: pika.adapters.blocking_connection.BlockingChannel, settings: Settings
) -> None:
    """Контроль к тесту выше: без `nack` возврат при закрытии соединения засчитывается."""
    RabbitMQJobQueue(TEST_RABBITMQ_URL or "").enqueue(JOB)

    def die(body: bytes) -> None:
        raise _ProcessDied

    with pytest.raises(_ProcessDied):
        build_worker(settings).run(die)

    assert _delivery_count(channel) == 1
