"""Сообщение, убивающее воркер на каждой доставке, уходит в DLQ, а не крутится вечно (#223).

Смерть - `WorkerDied` на чтении сообщения, до первой записи в базу: прогон не
успевает перейти в `failed`, и повторная доставка (спека `review-pipeline`) петлю
не прерывает. Её прерывает брокер: quorum-очередь считает доставки, вернувшиеся
при закрытии соединения умершего воркера, и после `DELIVERY_LIMIT` уводит
сообщение в DLX. Каждая смерть - новый воркер (`build_worker`), как перезапуск
контейнера.
"""

import json
import threading
import time

import pika
import pytest

from app.domain.enums import ReviewRunStatus
from app.infrastructure.queue.rabbitmq import (
    DELIVERY_LIMIT,
    DLQ_NAME,
    QUEUE_NAME,
)
from app.worker.factory import build_message_handler, build_worker

from ..conftest import requires_broker, requires_db
from ..e2e.stack import Stack
from .scenario import WorkerDied, accept, open_pull

pytestmark = [pytest.mark.integration, pytest.mark.chaos, requires_db, requires_broker]

DIE_TIMEOUT_SECONDS = 10


def _die_on_next_delivery(stack: Stack) -> None:
    """Новый воркер берёт сообщение и умирает на нём; дедлайн - на случай, если сообщения нет."""
    worker = build_worker(stack.settings)
    errors: list[BaseException] = []

    def die(body: bytes) -> None:
        raise WorkerDied

    def consume() -> None:
        try:
            worker.run(build_message_handler(die))
        except BaseException as error:  # noqa: BLE001 — переносится в поток теста
            errors.append(error)

    def cancel() -> None:
        worker.channel.cancel()

    thread = threading.Thread(target=consume, name="chaos-poison-worker", daemon=True)
    thread.start()
    thread.join(DIE_TIMEOUT_SECONDS)
    if thread.is_alive():
        worker.connection.add_callback_threadsafe(cancel)
        thread.join(DIE_TIMEOUT_SECONDS)
        raise AssertionError("воркер не получил сообщения: оно не вернулось в очередь")
    assert len(errors) == 1 and isinstance(errors[0], WorkerDied), errors


def _dead_letter(stack: Stack) -> tuple[str, dict[str, object]]:
    connection = pika.BlockingConnection(pika.URLParameters(stack.rabbitmq_url))
    try:
        method, properties, body = connection.channel().basic_get(DLQ_NAME, auto_ack=True)
    finally:
        connection.close()
    assert method is not None and body is not None and properties is not None
    return body, dict(properties.headers or {})


def test_a_message_killing_the_worker_on_every_delivery_ends_in_the_dlq(stack: Stack) -> None:
    run_id = accept(stack, open_pull(stack))

    # Первая доставка и `DELIVERY_LIMIT` повторных, потом - dead-letter.
    for _ in range(DELIVERY_LIMIT + 1):
        _die_on_next_delivery(stack)
    # Лидер quorum-очереди перекладывает сообщение в DLX асинхронно после обрыва соединения.
    deadline = time.monotonic() + DIE_TIMEOUT_SECONDS
    while stack.queue_depth(DLQ_NAME) == 0 and time.monotonic() < deadline:
        time.sleep(0.05)
    assert stack.queue_depth(DLQ_NAME) == 1, "сообщение не ушло в DLQ после лимита доставок"
    assert stack.queue_depth(QUEUE_NAME) == 0
    body, headers = _dead_letter(stack)
    assert json.loads(body)["review_run_id"] == run_id
    assert headers.get("x-first-death-reason") == "delivery_limit"
    # Прогон до записи не дошёл: его освободит выметатель по порогу `queued`.
    assert stack.run(run_id).status is ReviewRunStatus.QUEUED
