"""Адаптер очереди переживает закрытый канал и простоявшее соединение. Без брокера."""

from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pika
import pika.exceptions
import pytest

from app.domain.entities import ReviewJob
from app.infrastructure.queue import rabbitmq
from app.infrastructure.queue.rabbitmq import RabbitMQJobQueue

JOB = ReviewJob(
    id=UUID(int=1),
    event_type="pull_request",
    action="opened",
    installation_id=1,
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


class FlakyConnection:
    """Соединение, закрываемое брокером молча: `is_closed` не меняется, отказ
    всплывает первой публикацией — как после простоя в heartbeat."""

    def __init__(self, registry: list[FlakyConnection]) -> None:
        self.is_closed = False
        self.stale = False
        self.channels: list[FlakyChannel] = []
        registry.append(self)

    def channel(self) -> FlakyChannel:
        channel = FlakyChannel(self)
        self.channels.append(channel)
        return channel


class FlakyChannel:
    def __init__(self, connection: FlakyConnection) -> None:
        self.is_closed = False
        self.published = 0
        self._connection = connection

    def confirm_delivery(self) -> None: ...

    def basic_publish(self, **kwargs: Any) -> None:
        if self._connection.stale:
            raise pika.exceptions.AMQPConnectionError(
                320, "соединение закрыто брокером по heartbeat"
            )
        self.published += 1


def wire(monkeypatch, registry: list[FlakyConnection], fail: bool = False) -> RabbitMQJobQueue:
    def connect(params: object) -> FlakyConnection:
        connection = FlakyConnection(registry)
        connection.stale = fail
        return connection

    monkeypatch.setattr(pika, "BlockingConnection", connect)
    monkeypatch.setattr(rabbitmq, "declare_topology", lambda channel: None)
    monkeypatch.setattr(pika, "URLParameters", lambda url: SimpleNamespace())
    return RabbitMQJobQueue("amqp://x")


def test_a_stale_connection_is_reconnected_and_the_job_is_published(monkeypatch) -> None:
    """Брокер закрыл простоявшее соединение, а клиент об этом не знает:
    `is_closed` остаётся `False`, отказ всплывает первой публикацией.
    Адаптер сбрасывает обе стороны, переоткрывает соединение и публикует
    задачу заново — вызывающий исключения не видит."""
    connections: list[FlakyConnection] = []
    queue = wire(monkeypatch, connections)

    queue.enqueue(JOB)
    connections[0].stale = True  # брокер закрыл соединение после простоя
    queue.enqueue(JOB)

    assert len(connections) == 2
    assert connections[0].channels[0].published == 1  # первая доставка ушла до простоя
    assert connections[1].channels[0].published == 1  # задача ушла через новое соединение


def test_a_second_failure_after_reconnect_propagates(monkeypatch) -> None:
    """Брокер недоступен и после переоткрытия: исключение уходит вызывающему —
    прогон переведёт в `failed` компенсация use case, повторную доставку
    сделает хостинг."""
    connections: list[FlakyConnection] = []
    queue = wire(monkeypatch, connections, fail=True)

    with pytest.raises(pika.exceptions.AMQPConnectionError):
        queue.enqueue(JOB)

    assert len(connections) == 2
    assert connections[1].channels[0].published == 0
