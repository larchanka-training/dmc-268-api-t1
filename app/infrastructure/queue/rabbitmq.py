"""Адаптер `JobQueue` на RabbitMQ.

Тонкий: `to_wire_message`/`from_wire_message` — чистый перевод сущности в
формат из `docs/SYSTEM_DESIGN.md` §4.2 и обратно, `RabbitMQJobQueue` —
только транспорт и топология (декларация очереди/exchange'ей идемпотентна на
уровне AMQP, это не бизнес-правило). `from_wire_message` использует воркер
(`app/worker/factory.py`), чтобы разобрать сообщение, полученное из очереди,
обратно в `ReviewJob`.
"""

import json
import threading
from typing import Any
from uuid import UUID

import pika
from pika import spec
from pika.exchange_type import ExchangeType

from app.domain.entities import ReviewJob

QUEUE_NAME = "review_jobs"
DLX_NAME = "review_jobs.dlx"
DLQ_NAME = "review_jobs.dlq"
MAX_PRIORITY = 9
# Приоритет должен был бы зависеть от тарифа аккаунта, но `accounts` ещё не
# реализован (см. BACKEND_ARCHITECTURE.md, "Спроектированы, но не
# реализованы") — фиксированный дефолт до тех пор, см. design.md
# change'а add-rabbitmq-job-queue.
DEFAULT_PRIORITY = 5


def to_wire_message(job: ReviewJob) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "event_type": job.event_type,
        "action": job.action,
        "repository": {
            "id": job.repository_provider_id,
            "full_name": job.repository_full_name,
        },
        "pull_request": {
            "number": job.pull_request_number,
            "head_sha": job.head_sha,
            "base_sha": job.base_sha,
        },
    }


def from_wire_message(data: bytes) -> ReviewJob:
    message = json.loads(data)
    return ReviewJob(
        id=UUID(message["job_id"]),
        event_type=message["event_type"],
        action=message["action"],
        repository_provider_id=message["repository"]["id"],
        repository_full_name=message["repository"]["full_name"],
        pull_request_number=message["pull_request"]["number"],
        head_sha=message["pull_request"]["head_sha"],
        base_sha=message["pull_request"]["base_sha"],
    )


def declare_topology(channel: pika.adapters.blocking_connection.BlockingChannel) -> None:
    """Объявить DLX, DLQ и основную очередь. Идемпотентно, можно звать при каждом подключении."""
    # Игнор ниже — баг в стабах types-pika: `ExchangeType.fanout` там
    # аннотирован как `str`, хотя рантайм-класс — полноценный str-enum.
    channel.exchange_declare(
        exchange=DLX_NAME,
        exchange_type=ExchangeType.fanout,  # type: ignore[arg-type]
        durable=True,
    )
    channel.queue_declare(queue=DLQ_NAME, durable=True)
    channel.queue_bind(queue=DLQ_NAME, exchange=DLX_NAME)
    channel.queue_declare(
        queue=QUEUE_NAME,
        durable=True,
        arguments={
            "x-max-priority": MAX_PRIORITY,
            "x-dead-letter-exchange": DLX_NAME,
        },
    )


class RabbitMQJobQueue:
    """Реализация порта `JobQueue`.

    Держит одно соединение на весь процесс и переиспользует его между
    вызовами `enqueue`, а не открывает handshake заново на каждый вебхук —
    `enqueue` лежит на синхронном горячем пути HTTP-запроса. Пересоздаёт
    соединение, только если прежнее закрыто (брокер перезапустился, простой
    оборвал канал). `BlockingConnection` не потокобезопасен, а FastAPI гоняет
    sync-эндпоинты в threadpool, поэтому доступ сериализован локом.
    """

    def __init__(self, url: str) -> None:
        self._url = url
        self._lock = threading.Lock()
        self._connection: pika.BlockingConnection | None = None
        self._channel: pika.adapters.blocking_connection.BlockingChannel | None = None

    def _channel_ready(self) -> pika.adapters.blocking_connection.BlockingChannel:
        if self._connection is None or self._connection.is_closed:
            self._connection = pika.BlockingConnection(pika.URLParameters(self._url))
            self._channel = self._connection.channel()
            declare_topology(self._channel)
            # Publisher confirms: без них `basic_publish` на `BlockingConnection`
            # только пишет во внутренний буфер и возвращается, не дожидаясь,
            # чтобы брокер реально принял сообщение — раньше это скрывал
            # `connection.close()` на каждый вызов (его handshake попутно ждал
            # отправки), но при переиспользуемом соединении обращение к
            # соседней очереди сразу после `enqueue` могло не увидеть
            # сообщение. С `confirm_delivery` `basic_publish` блокируется до
            # ack/nack брокера.
            self._channel.confirm_delivery()
        assert self._channel is not None
        return self._channel

    def enqueue(self, job: ReviewJob) -> None:
        with self._lock:
            channel = self._channel_ready()
            channel.basic_publish(
                exchange="",
                routing_key=QUEUE_NAME,
                body=json.dumps(to_wire_message(job)).encode("utf-8"),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=spec.PERSISTENT_DELIVERY_MODE,
                    priority=DEFAULT_PRIORITY,
                ),
                mandatory=True,
            )
