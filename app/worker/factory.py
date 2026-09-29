"""Собирает consume-цикл воркера и доменный обработчик, который в нём крутится.

`Worker`/`build_worker` не знают о доменном пайплайне: принимают обработчик
сообщением-байтами. `build_review_handler` — сборка настоящего обработчика
(разбор сообщения + `run_review`), а `run_worker` — то, что вызывает
`app/worker/__main__.py`, чтобы не импортировать `app.infrastructure`/
`app.application` напрямую (точка входа обязана оставаться тонкой).
Ack/nack-решение — чистая `should_ack`, здесь только вызовы канала.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

import pika

from app.application.ports import UnitOfWork
from app.application.ports.llm_gateway import LlmGateway
from app.application.review_pipeline import run_review
from app.config import Settings
from app.domain.ids import new_id
from app.infrastructure.container import build_container
from app.infrastructure.queue.rabbitmq import (
    QUEUE_NAME,
    declare_topology,
    from_wire_message,
)
from app.worker.handling import should_ack

MessageHandler = Callable[[bytes], None]


@dataclass(frozen=True, slots=True)
class Worker:
    connection: pika.BlockingConnection
    channel: pika.adapters.blocking_connection.BlockingChannel

    def run(self, handler: MessageHandler, *, limit: int | None = None) -> None:
        """Потреблять `review_jobs`.

        `limit=None` — бесконечно (боевой запуск `python -m app.worker`);
        конечное число — для тестов, чтобы не держать процесс висящим на
        пустой очереди. В любом случае при выходе отменяем регистрацию
        консьюмера и закрываем соединение — иначе брокер продолжает считать
        канал живым консьюмером и делит с ним следующие сообщения, хотя
        Python-цикл их уже не читает.
        """
        processed = 0
        try:
            for method, _properties, body in self.channel.consume(
                QUEUE_NAME, inactivity_timeout=1
            ):
                if method is None:
                    continue
                # Стаб типизирует элементы кортежа независимо, поэтому
                # проверка `method` не сужает `body`/`delivery_tag`.
                assert body is not None
                assert method.delivery_tag is not None
                exc: BaseException | None = None
                try:
                    handler(body)
                except Exception as caught:  # noqa: BLE001 — любая ошибка уводит сообщение в DLQ, а не роняет процесс
                    exc = caught
                if should_ack(exc):
                    self.channel.basic_ack(method.delivery_tag)
                else:
                    self.channel.basic_nack(method.delivery_tag, requeue=False)
                processed += 1
                if limit is not None and processed >= limit:
                    break
        finally:
            self.channel.cancel()
            self.connection.close()


def build_worker(settings: Settings) -> Worker:
    connection = pika.BlockingConnection(pika.URLParameters(settings.rabbitmq_url))
    channel = connection.channel()
    declare_topology(channel)
    channel.basic_qos(prefetch_count=1)
    return Worker(connection=connection, channel=channel)


class ReviewHandlerDeps(Protocol):
    """Только то, что нужно обработчику — не весь `Container`."""

    def unit_of_work(self) -> UnitOfWork: ...

    def llm_gateway(self) -> LlmGateway: ...


def build_review_handler(deps: ReviewHandlerDeps) -> MessageHandler:
    def handle(body: bytes) -> None:
        job = from_wire_message(body)
        run_review(
            job,
            uow=deps.unit_of_work(),
            llm_gateway=deps.llm_gateway(),
            now=datetime.now(UTC),
            new_id=new_id,
        )

    return handle


def run_worker(settings: Settings) -> None:
    """Собрать зависимости и запустить воркер бесконечно. Вызывается из `__main__.py`."""
    handler = build_review_handler(build_container(settings))
    build_worker(settings).run(handler)
