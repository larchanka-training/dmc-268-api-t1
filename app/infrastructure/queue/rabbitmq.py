"""Адаптер `JobQueue` для RabbitMQ на pika.

Порт не импортируется — совпадение структурное (`.agents/rules/backend.md`,
«Швы и порты»). Тело сообщения — только доменные данные строго по
`docs/SYSTEM_DESIGN.md` §4.2; приоритет и долговечность — метаданные
доставки, они едут свойствами AMQP и в тело не дублируются.

Имя очереди `review.jobs` в документах не зафиксировано: единственная
очередь §4.2, точечной нотацией «сервис.назначение»; адаптер владеет
константой, воркер живёт в этом же кодбейсе и импортирует её же.

Конструктор не соединяется с брокером: без injected-канала подключение
открывается на каждый `enqueue` и закрывается после — `BlockingConnection`
не потокобезопасен, а API-процесс зовёт enqueue из threadpool'а. Канал
инъектируется тестовым двойником, тогда брокер не нужен вовсе.
"""

import json
from typing import Any, Protocol

import pika

from app.domain.entities import ReviewJob

QUEUE_NAME = "review.jobs"
# §4.2: приоритеты сообщений 0–9; запас сверху стоит дешевле, чем коллизия
# с сообщением приоритета 10 у объявленной очереди.
MAX_PRIORITY = 10
_EVENT_TYPE = "pull_request"


class RabbitMqConfigError(RuntimeError):
    """Адаптеру не с чем соединяться: RABBITMQ_URL не задан."""


class _Channel(Protocol):
    """Шов канала: pika-канал структурно, двойник в тестах — буквально."""

    def queue_declare(
        self,
        queue: str,
        durable: bool = ...,
        arguments: dict[str, Any] | None = ...,
    ) -> object: ...

    def basic_publish(
        self,
        exchange: str,
        routing_key: str,
        body: bytes,
        properties: pika.BasicProperties = ...,
    ) -> object: ...


def _serialize(job: ReviewJob) -> bytes:
    body = {
        "job_id": str(job.job_id),
        "event_type": _EVENT_TYPE,
        "action": job.action,
        "repository": {
            "full_name": job.repository_full_name,
            "id": job.repository_provider_id,
        },
        "pull_request": {
            "number": job.pr_number,
            "head_sha": job.head_sha,
            "base_sha": job.base_sha,
        },
    }
    return json.dumps(body).encode("utf-8")


class PikaJobQueue:
    """Публикация задач ревью в единственную очередь с приоритетами."""

    def __init__(self, url: str = "", channel: _Channel | None = None) -> None:
        self._url = url
        self._channel = channel

    def enqueue(self, job: ReviewJob) -> None:
        if self._channel is not None:
            self._publish(self._channel, job)
            return
        if not self._url:
            raise RabbitMqConfigError(
                "RABBITMQ_URL не задан: очередь задач недоступна, "
                "задайте строку подключения AMQP в настройках"
            )
        connection = pika.BlockingConnection(pika.URLParameters(self._url))
        try:
            self._publish(connection.channel(), job)
        finally:
            connection.close()

    def _publish(self, channel: _Channel, job: ReviewJob) -> None:
        # durable: persistent-сообщение (delivery_mode=2) в недолговечной
        # очереди переживало бы только до рестарта брокера.
        channel.queue_declare(
            queue=QUEUE_NAME,
            durable=True,
            arguments={"x-max-priority": MAX_PRIORITY},
        )
        channel.basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=_serialize(job),
            properties=pika.BasicProperties(
                delivery_mode=2,
                content_type="application/json",
                priority=job.priority,
            ),
        )
