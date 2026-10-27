"""Интеграционный тест адаптера `JobQueue`. Нужен живой RabbitMQ."""

import json
import time
from collections.abc import Iterator
from uuid import UUID

import pika
import pytest

from app.domain.entities import ReviewJob
from app.infrastructure.queue.rabbitmq import (
    DEFAULT_PRIORITY,
    DLQ_NAME,
    DLX_NAME,
    LEGACY_QUEUE_NAME,
    QUEUE_NAME,
    RabbitMQJobQueue,
    declare_topology,
    move_legacy_jobs,
    to_wire_message,
)

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


def test_enqueue_publishes_the_wire_message_with_default_priority(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    assert TEST_RABBITMQ_URL is not None
    RabbitMQJobQueue(TEST_RABBITMQ_URL).enqueue(JOB)

    method, properties, body = channel.basic_get(QUEUE_NAME, auto_ack=True)
    assert method is not None
    assert body is not None
    assert json.loads(body) == {
        "job_id": str(JOB.id),
        "review_run_id": str(JOB.review_run_id),
        "event_type": "pull_request",
        "action": "opened",
        "installation_id": 512804923,
        "repository": {"id": "987654", "full_name": "owner/repo"},
        "pull_request": {"number": 42, "head_sha": "abc123def", "base_sha": "fed654cba"},
    }
    assert properties is not None
    assert properties.priority == DEFAULT_PRIORITY


def test_nacked_message_lands_in_the_dead_letter_queue(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    assert TEST_RABBITMQ_URL is not None
    RabbitMQJobQueue(TEST_RABBITMQ_URL).enqueue(JOB)

    method, _, _ = channel.basic_get(QUEUE_NAME, auto_ack=False)
    assert method is not None
    assert method.delivery_tag is not None
    channel.basic_nack(method.delivery_tag, requeue=False)

    # Dead-lettering — асинхронная операция брокера: сообщение не появляется
    # в DLQ мгновенно в рамках того же `basic_nack`, поэтому опрашиваем
    # вместо одного немедленного `basic_get`.
    dlq_method = None
    dlq_body: str | None = None
    for _ in range(20):
        dlq_method, _, dlq_body = channel.basic_get(DLQ_NAME, auto_ack=True)
        if dlq_method is not None:
            break
        time.sleep(0.05)

    assert dlq_method is not None
    assert dlq_body is not None
    assert json.loads(dlq_body)["job_id"] == str(JOB.id)


# --- переход на quorum-очередь (#223) -------------------------------------


@pytest.fixture
def legacy_queue(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> Iterator[pika.adapters.blocking_connection.BlockingChannel]:
    """Классическая `review_jobs` в том виде, в каком её объявлял код до #223."""
    channel.queue_declare(
        queue=LEGACY_QUEUE_NAME,
        durable=True,
        arguments={"x-max-priority": 9, "x-dead-letter-exchange": DLX_NAME},
    )
    channel.queue_purge(LEGACY_QUEUE_NAME)
    try:
        yield channel
    finally:
        channel.queue_delete(LEGACY_QUEUE_NAME)


def test_legacy_jobs_move_to_the_quorum_queue_with_their_properties(
    legacy_queue: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    assert TEST_RABBITMQ_URL is not None
    traceparent = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    legacy_queue.basic_publish(
        exchange="",
        routing_key=LEGACY_QUEUE_NAME,
        body=json.dumps(to_wire_message(JOB)).encode("utf-8"),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE,
            priority=DEFAULT_PRIORITY,
            headers={"traceparent": traceparent},
        ),
    )
    connection = pika.BlockingConnection(pika.URLParameters(TEST_RABBITMQ_URL))
    try:
        assert move_legacy_jobs(connection) == 1
        assert move_legacy_jobs(connection) == 0  # старая очередь пуста, но остаётся
    finally:
        connection.close()

    method, properties, body = legacy_queue.basic_get(QUEUE_NAME, auto_ack=True)
    assert method is not None and body is not None and properties is not None
    assert json.loads(body)["job_id"] == str(JOB.id)
    assert properties.priority == DEFAULT_PRIORITY
    assert properties.delivery_mode == pika.spec.PERSISTENT_DELIVERY_MODE
    assert (properties.headers or {}).get("traceparent") == traceparent
    assert legacy_queue.queue_declare(LEGACY_QUEUE_NAME, passive=True).method.message_count == 0


def test_without_a_legacy_queue_nothing_moves_and_the_connection_stays_usable(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    assert TEST_RABBITMQ_URL is not None
    connection = pika.BlockingConnection(pika.URLParameters(TEST_RABBITMQ_URL))
    try:
        consuming = connection.channel()
        assert move_legacy_jobs(connection) == 0
        # `404` пассивного объявления закрыл только свой канал.
        assert consuming.is_open
        assert consuming.queue_declare(QUEUE_NAME, passive=True).method.message_count == 0
    finally:
        connection.close()
