"""Heartbeat воркера и повтор не начатого прогона на живом RabbitMQ (#157).

Обработчик и проход очистки идут в своём потоке, а цикл соединения отвечает брокеру:
обработка, очистка и пауза перед повтором дольше двух интервалов heartbeat не рвут
соединение, и ack доходит. До правки они шли в главном потоке — брокер закрывал соединение
по пропущенным heartbeat, а `basic_ack` падал `StreamLostError`.
"""

import json
import time
from collections.abc import Iterator
from uuid import UUID

import pika
import pytest

from app.application.review_pipeline import ReviewNotStarted
from app.config import Settings
from app.domain.entities import ReviewJob
from app.infrastructure.queue.rabbitmq import (
    DELIVERY_LIMIT,
    DLQ_NAME,
    QUEUE_NAME,
    RabbitMQJobQueue,
    declare_topology,
)
from app.worker.factory import build_worker

from ..conftest import TEST_RABBITMQ_URL, requires_broker

pytestmark = [pytest.mark.integration, requires_broker]

HEARTBEAT_SECONDS = 1

# Лидер quorum-очереди перекладывает сообщение в DLX асинхронно.
DEAD_LETTER_TIMEOUT_SECONDS = 10

JOB = ReviewJob(
    id=UUID(int=11),
    review_run_id=UUID(int=12),
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


def _count(ch: pika.adapters.blocking_connection.BlockingChannel, queue: str) -> int:
    count = ch.queue_declare(queue, passive=True).method.message_count
    assert count is not None
    return count


def _await_count(
    ch: pika.adapters.blocking_connection.BlockingChannel, queue: str, expected: int
) -> int:
    """Счётчик очереди, дождавшись `expected`: quorum-очередь применяет возврат асинхронно."""
    deadline = time.monotonic() + DEAD_LETTER_TIMEOUT_SECONDS
    while (count := _count(ch, queue)) != expected and time.monotonic() < deadline:
        time.sleep(0.05)
    return count


def test_a_handler_longer_than_two_heartbeats_keeps_the_connection_and_acks(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    RabbitMQJobQueue(TEST_RABBITMQ_URL).enqueue(JOB)  # type: ignore[arg-type]
    handled: list[bytes] = []

    def slow(body: bytes) -> None:
        time.sleep(HEARTBEAT_SECONDS * 5)
        handled.append(body)

    build_worker(_settings(), heartbeat=HEARTBEAT_SECONDS).run(slow, limit=1)

    assert handled
    # ack дошёл: сообщение не вернулось в очередь и не ушло в DLQ.
    assert _count(channel, QUEUE_NAME) == 0
    assert _count(channel, DLQ_NAME) == 0


def test_a_run_not_started_is_redelivered_and_then_acked(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    RabbitMQJobQueue(TEST_RABBITMQ_URL).enqueue(JOB)  # type: ignore[arg-type]

    def not_started(body: bytes) -> None:
        raise ReviewNotStarted("база недоступна")

    build_worker(_settings()).run(not_started, limit=1, retry_delay=0)

    assert _count(channel, DLQ_NAME) == 0
    redelivered: list[bool] = []

    def handled(body: bytes) -> None:
        redelivered.append(True)

    build_worker(_settings()).run(handled, limit=1)

    assert redelivered == [True]
    assert _count(channel, QUEUE_NAME) == 0
    assert _count(channel, DLQ_NAME) == 0


def test_a_sweep_longer_than_two_heartbeats_keeps_the_connection_and_acks(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    RabbitMQJobQueue(TEST_RABBITMQ_URL).enqueue(JOB)  # type: ignore[arg-type]
    handled: list[bytes] = []

    def slow_sweep() -> None:
        time.sleep(HEARTBEAT_SECONDS * 5)

    build_worker(_settings(), heartbeat=HEARTBEAT_SECONDS).run(
        handled.append, limit=1, sweep=slow_sweep, sweep_interval=0
    )

    assert handled
    assert _count(channel, QUEUE_NAME) == 0
    assert _count(channel, DLQ_NAME) == 0


def test_a_retry_pause_longer_than_two_heartbeats_keeps_the_connection(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    RabbitMQJobQueue(TEST_RABBITMQ_URL).enqueue(JOB)  # type: ignore[arg-type]

    def not_started(body: bytes) -> None:
        raise ReviewNotStarted("база недоступна")

    build_worker(_settings(), heartbeat=HEARTBEAT_SECONDS).run(
        not_started, limit=1, retry_delay=HEARTBEAT_SECONDS * 3
    )

    # reject дошёл после паузы: сообщение снова в очереди, а не в DLQ.
    assert _await_count(channel, QUEUE_NAME, 1) == 1
    assert _count(channel, DLQ_NAME) == 0


def test_a_run_never_started_ends_in_the_dlq_after_the_delivery_limit(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    RabbitMQJobQueue(TEST_RABBITMQ_URL).enqueue(JOB)  # type: ignore[arg-type]
    attempts: list[bytes] = []

    def not_started(body: bytes) -> None:
        attempts.append(body)
        raise ReviewNotStarted("база недоступна")

    # Первая доставка и `DELIVERY_LIMIT` повторных, потом — dead-letter.
    build_worker(_settings()).run(not_started, limit=DELIVERY_LIMIT + 1, retry_delay=0)

    assert _await_count(channel, DLQ_NAME, 1) == 1, "сообщение не ушло в DLQ после лимита"
    assert len(attempts) == DELIVERY_LIMIT + 1
    assert _count(channel, QUEUE_NAME) == 0
    method, properties, body = channel.basic_get(DLQ_NAME, auto_ack=True)
    assert method is not None and properties is not None and body is not None
    assert json.loads(body)["review_run_id"] == str(JOB.review_run_id)
    assert (properties.headers or {}).get("x-first-death-reason") == "delivery_limit"
