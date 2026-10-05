"""Адаптер очереди перепроверяет канал, а не только соединение. Без брокера."""

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pika
import pytest

from app.domain.entities import ReviewJob
from app.infrastructure.queue import rabbitmq
from app.infrastructure.queue.rabbitmq import RabbitMQJobQueue

JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
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


def test_a_stale_connection_is_reopened_and_the_publish_retried_once(monkeypatch) -> None:
    connections: list[Any] = []

    class StaleChannel(FakeChannel):
        def basic_publish(self, **kwargs: Any) -> None:
            raise pika.exceptions.StreamLostError("broker closed the idle connection")

    class FakeConnection:
        is_closed = False  # pika узнаёт об обрыве только при записи

        def __init__(self, stale: bool) -> None:
            self.stale = stale
            self.closed_by_us = False
            self.channels: list[FakeChannel] = []

        def channel(self) -> FakeChannel:
            self.channels.append(StaleChannel() if self.stale else FakeChannel())
            return self.channels[-1]

        def close(self) -> None:
            self.closed_by_us = True

    def connect(params: Any) -> FakeConnection:
        connection = FakeConnection(stale=not connections)
        connections.append(connection)
        return connection

    monkeypatch.setattr(pika, "BlockingConnection", connect)
    monkeypatch.setattr(rabbitmq, "declare_topology", lambda channel: None)
    monkeypatch.setattr(pika, "URLParameters", lambda url: SimpleNamespace())
    queue = RabbitMQJobQueue("amqp://x")

    queue.enqueue(JOB)

    assert len(connections) == 2
    assert connections[0].closed_by_us
    assert connections[1].channels[0].published == 1


def test_a_second_connection_failure_is_not_swallowed(monkeypatch) -> None:
    class DeadChannel(FakeChannel):
        def basic_publish(self, **kwargs: Any) -> None:
            raise pika.exceptions.StreamLostError("broker is down")

    class FakeConnection:
        is_closed = False

        def channel(self) -> FakeChannel:
            return DeadChannel()

        def close(self) -> None: ...

    monkeypatch.setattr(pika, "BlockingConnection", lambda params: FakeConnection())
    monkeypatch.setattr(rabbitmq, "declare_topology", lambda channel: None)
    monkeypatch.setattr(pika, "URLParameters", lambda url: SimpleNamespace())

    with pytest.raises(pika.exceptions.StreamLostError):
        RabbitMQJobQueue("amqp://x").enqueue(JOB)
