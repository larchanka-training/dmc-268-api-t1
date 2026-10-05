"""Собирает consume-цикл воркера и доменный обработчик, который в нём крутится.

`Worker`/`build_worker` не знают о доменном пайплайне: принимают обработчик
сообщением-байтами. `build_review_handler` — сборка настоящего обработчика
(разбор сообщения + `run_review`), а `run_worker` — то, что вызывает
`app/worker/__main__.py`, чтобы не импортировать `app.infrastructure`/
`app.application` напрямую (точка входа обязана оставаться тонкой).
Ack/nack-решение — чистая `should_ack`, здесь только вызовы канала.
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

import pika

from app.application.ports import UnitOfWork
from app.application.ports.llm_gateway import LlmGateway
from app.application.review_pipeline import run_review
from app.application.stale_sweep import sweep_stale_runs
from app.config import Settings
from app.domain.ids import new_id
from app.infrastructure.container import build_container
from app.infrastructure.queue.rabbitmq import (
    QUEUE_NAME,
    declare_topology,
    from_wire_message,
)
from app.worker.handling import should_ack

logger = logging.getLogger(__name__)

MessageHandler = Callable[[bytes], None]

# Как часто выметать зависшие прогоны. Проход дёргается на каждом такте
# consume-цикла (простой раз в секунду или очередное сообщение) и сам
# ограничивается этим интервалом.
SWEEP_INTERVAL_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class Worker:
    connection: pika.BlockingConnection
    channel: pika.adapters.blocking_connection.BlockingChannel

    def run(
        self,
        handler: MessageHandler,
        *,
        limit: int | None = None,
        sweep: Callable[[], object] | None = None,
        sweep_interval: float = SWEEP_INTERVAL_SECONDS,
    ) -> None:
        """Потреблять `review_jobs`.

        `limit=None` — бесконечно (боевой запуск `python -m app.worker`);
        конечное число — для тестов, чтобы не держать процесс висящим на
        пустой очереди. В любом случае при выходе отменяем регистрацию
        консьюмера и закрываем соединение — иначе брокер продолжает считать
        канал живым консьюмером и делит с ним следующие сообщения, хотя
        Python-цикл их уже не читает.

        `sweep` — периодический проход по зависшим прогонам; его сбой
        логируется и не останавливает потребление.
        """
        processed = 0
        last_sweep: float | None = None
        try:
            for method, _properties, body in self.channel.consume(
                QUEUE_NAME, inactivity_timeout=1
            ):
                if sweep is not None and (
                    last_sweep is None or time.monotonic() - last_sweep >= sweep_interval
                ):
                    last_sweep = time.monotonic()
                    try:
                        sweep()
                    except Exception:
                        logger.exception("проход по зависшим прогонам завершился ошибкой")
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
    parameters = pika.URLParameters(settings.rabbitmq_url)
    # Обработчик вызывается синхронно и ходит минутами (запрос к модели), а
    # пока он работает, цикл событий `BlockingConnection` не крутится и
    # heartbeat не отправляется: брокер по умолчанию рвёт такое соединение, и
    # `basic_ack` падает `StreamLostError`. Без heartbeat обрыв не случается.
    parameters.heartbeat = 0
    connection = pika.BlockingConnection(parameters)
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
        try:
            run_review(
                job,
                uow=deps.unit_of_work(),
                llm_gateway=deps.llm_gateway(),
                now=lambda: datetime.now(UTC),
                monotonic=time.monotonic,
                new_id=new_id,
            )
        except Exception:
            # Сообщение уйдёт в DLQ; по job_id видно, какая это постановка
            # прогона, по review_run_id — какой прогон.
            logger.exception(
                "задача %s (прогон %s) завершилась ошибкой", job.id, job.review_run_id
            )
            raise

    return handle


def run_worker(settings: Settings) -> None:
    """Собрать зависимости и запустить воркер бесконечно. Вызывается из `__main__.py`."""
    container = build_container(settings)
    limit = timedelta(seconds=settings.stale_run_timeout_seconds)

    def sweep() -> None:
        swept = sweep_stale_runs(container.unit_of_work(), now=datetime.now(UTC), limit=limit)
        if swept:
            logger.warning("зависших прогонов переведено в failed: %d", swept)

    build_worker(settings).run(build_review_handler(container), sweep=sweep)
