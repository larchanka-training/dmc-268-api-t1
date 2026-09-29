"""Адаптер `JobQueue` на RabbitMQ.

Тонкий: `to_wire_message` — чистый перевод сущности в формат из
`docs/SYSTEM_DESIGN.md` §4.2, `RabbitMQJobQueue` — только транспорт и
топология (декларация очереди/exchange'ей идемпотентна на уровне AMQP, это
не бизнес-правило).
"""

import json
from typing import Any

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
    """Реализация порта `JobQueue`."""

    def __init__(self, url: str) -> None:
        self._url = url

    def enqueue(self, job: ReviewJob) -> None:
        connection = pika.BlockingConnection(pika.URLParameters(self._url))
        try:
            channel = connection.channel()
            declare_topology(channel)
            channel.basic_publish(
                exchange="",
                routing_key=QUEUE_NAME,
                body=json.dumps(to_wire_message(job)).encode("utf-8"),
                properties=pika.BasicProperties(
                    content_type="application/json",
                    delivery_mode=spec.PERSISTENT_DELIVERY_MODE,
                    priority=DEFAULT_PRIORITY,
                ),
            )
        finally:
            connection.close()
