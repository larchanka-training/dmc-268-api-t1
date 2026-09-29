"""`PikaJobQueue` на фейковом канале: тело §4.2, приоритет в свойствах AMQP.

Канал — двойник из `tests.fakes`, записывающий `queue_declare` и
`basic_publish`: брокера нет, патчить вендорский pika не нужно. Ожидаемое
тело — dict-литерал из `docs/SYSTEM_DESIGN.md` §4.2, а не пересобранная
сериализатором структура.
"""

import json
from uuid import UUID

import pytest

from app.domain.entities import ReviewJob
from app.infrastructure.queue.rabbitmq import PikaJobQueue, RabbitMqConfigError

from ..fakes import FakeChannel, Published

QUEUE_NAME = "review.jobs"

JOB = ReviewJob(
    job_id=UUID("0192f0c0-0000-7000-8000-000000000001"),
    review_run_id=UUID("0192f0c0-0000-7000-8000-000000000002"),
    repository_full_name="larchanka-training/dmc-268-api-t1",
    repository_provider_id=923478362,
    pr_number=6,
    head_sha="a1b2c3d4e5f6789012345678abcdef0123456789",
    base_sha="fed654cba0fed654cba0fed654cba0fed654cba0",
    action="opened",
    priority=5,
)


def make_queue(channel: FakeChannel) -> PikaJobQueue:
    return PikaJobQueue(url="amqp://test:test@localhost/test", channel=channel)


def enqueue(channel: FakeChannel) -> Published:
    make_queue(channel).enqueue(JOB)
    assert len(channel.published) == 1
    return channel.published[0]


def test_publishes_body_exactly_per_section_4_2() -> None:
    """Тело — доменные данные литералом из §4.2, и ничего больше."""
    channel = FakeChannel()
    published = enqueue(channel)
    assert published.exchange == ""
    assert published.routing_key == QUEUE_NAME
    assert json.loads(published.body) == {
        "job_id": "0192f0c0-0000-7000-8000-000000000001",
        "event_type": "pull_request",
        "action": "opened",
        "repository": {
            "full_name": "larchanka-training/dmc-268-api-t1",
            "id": 923478362,
        },
        "pull_request": {
            "number": 6,
            "head_sha": "a1b2c3d4e5f6789012345678abcdef0123456789",
            "base_sha": "fed654cba0fed654cba0fed654cba0fed654cba0",
        },
    }


def test_priority_is_amqp_property_not_a_body_field() -> None:
    channel = FakeChannel()
    published = enqueue(channel)
    assert published.properties.priority == 5
    assert "priority" not in json.loads(published.body)


def test_queue_is_declared_with_max_priority() -> None:
    channel = FakeChannel()
    enqueue(channel)
    assert channel.declared == [
        {"queue": QUEUE_NAME, "durable": True, "arguments": {"x-max-priority": 10}}
    ]


def test_message_is_persistent_json() -> None:
    channel = FakeChannel()
    published = enqueue(channel)
    assert published.properties.delivery_mode == 2
    assert published.properties.content_type == "application/json"


def test_missing_base_sha_serializes_as_null() -> None:
    channel = FakeChannel()
    make_queue(channel).enqueue(
        ReviewJob(
            job_id=JOB.job_id,
            review_run_id=JOB.review_run_id,
            repository_full_name=JOB.repository_full_name,
            repository_provider_id=JOB.repository_provider_id,
            pr_number=JOB.pr_number,
            head_sha=JOB.head_sha,
            base_sha=None,
            action="synchronize",
            priority=0,
        )
    )
    body = json.loads(channel.published[0].body)
    assert body["pull_request"]["base_sha"] is None


def test_enqueue_without_url_and_channel_fails_loudly() -> None:
    """Пустой RABBITMQ_URL — понятная ошибка, а не тихий успех или таймаут."""
    with pytest.raises(RabbitMqConfigError, match="RABBITMQ_URL"):
        PikaJobQueue().enqueue(JOB)
