"""Heartbeat воркера и повтор не начатого прогона на живом RabbitMQ (#157).

Обработчик идёт в своём потоке, а цикл соединения отвечает брокеру: обработка дольше двух
интервалов heartbeat не рвёт соединение, и ack доходит. До правки обработчик шёл в главном
потоке — брокер закрывал соединение по пропущенным heartbeat, а `basic_ack` падал
`StreamLostError`.
"""

import time
from collections.abc import Iterator
from uuid import UUID

import pika
import pytest

from app.application.review_pipeline import ReviewNotStarted
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

HEARTBEAT_SECONDS = 1

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

    build_worker(_settings()).run(not_started, limit=1)

    assert _count(channel, DLQ_NAME) == 0
    redelivered: list[bool] = []

    def handled(body: bytes) -> None:
        redelivered.append(True)

    build_worker(_settings()).run(handled, limit=1)

    assert redelivered == [True]
    assert _count(channel, QUEUE_NAME) == 0
    assert _count(channel, DLQ_NAME) == 0
