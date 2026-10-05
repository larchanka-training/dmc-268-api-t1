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
    QUEUE_NAME,
    RabbitMQJobQueue,
    declare_topology,
)

from ..conftest import TEST_RABBITMQ_URL, requires_broker

pytestmark = [pytest.mark.integration, requires_broker]

JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    event_type="pull_request",
    action="opened",
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
