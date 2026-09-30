"""Адаптер очереди перепроверяет канал, а не только соединение. Без брокера."""

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pika

from app.domain.entities import ReviewJob
from app.infrastructure.queue import rabbitmq
from app.infrastructure.queue.rabbitmq import RabbitMQJobQueue

JOB = ReviewJob(
    id=UUID(int=1),
    event_type="pull_request",
    action="opened",
    repository_provider_id="1",
    repository_full_name="o/r",
    pull_request_number=1,
    head_sha="a",
    base_sha="b",
)


class FakeChannel:
    def __init__(self) -> None:
        self.is_closed = False
        self.published = 0

    def confirm_delivery(self) -> None: ...

    def basic_publish(self, **kwargs: Any) -> None:
        self.published += 1


def test_a_closed_channel_on_a_live_connection_is_reopened(monkeypatch) -> None:
    channels: list[FakeChannel] = []

    class FakeConnection:
        is_closed = False

        def channel(self) -> FakeChannel:
            channels.append(FakeChannel())
            return channels[-1]

    monkeypatch.setattr(pika, "BlockingConnection", lambda params: FakeConnection())
    monkeypatch.setattr(rabbitmq, "declare_topology", lambda channel: None)
    monkeypatch.setattr(pika, "URLParameters", lambda url: SimpleNamespace())
    queue = RabbitMQJobQueue("amqp://x")

    queue.enqueue(JOB)
    channels[0].is_closed = True  # ошибка уровня канала, соединение живо
    queue.enqueue(JOB)

    assert len(channels) == 2
    assert channels[1].published == 1
