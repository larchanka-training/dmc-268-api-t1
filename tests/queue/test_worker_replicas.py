"""Несколько воркеров на одной очереди на живом RabbitMQ (#99, DoD).

Две задачи, пришедшие одновременно, обрабатываются параллельно разными воркерами. Воркер —
`build_worker`, как в `python -m app.worker`: своё соединение, `prefetch_count=1`. Пока первая
задача не подтверждена, брокер не отдаст тому же воркеру вторую, и она уходит второму
потребителю очереди.

Параллельность проверяет барьер на двоих в обработчике: он пропускает, только когда в нём
оба обработчика разом. Если обе задачи достались одному воркеру по очереди, первая ждала бы
у барьера одна и сломала его по таймауту — тест падает.
"""

import threading
from collections.abc import Iterator
from dataclasses import replace
from uuid import UUID

import pika
import pytest

from app.config import Settings
from app.domain.entities import ReviewJob
from app.infrastructure.queue.rabbitmq import (
    DLQ_NAME,
    QUEUE_NAME,
    RabbitMQJobQueue,
    declare_topology,
)
from app.worker.factory import build_worker

from ..conftest import TEST_RABBITMQ_URL, requires_broker

pytestmark = [pytest.mark.integration, requires_broker]

# Сколько обработчик ждёт второго у барьера и сколько тест ждёт воркеров.
BARRIER_TIMEOUT_SECONDS = 15
JOIN_TIMEOUT_SECONDS = 60

JOB = ReviewJob(
    id=UUID(int=21),
    review_run_id=UUID(int=22),
    event_type="pull_request",
    action="opened",
    installation_id=512804923,
    repository_provider_id="987654",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="abc123def",
    base_sha="fed654cba",
)


@pytest.fixture
def channel() -> Iterator[pika.adapters.blocking_connection.BlockingChannel]:
    assert TEST_RABBITMQ_URL is not None
    connection = pika.BlockingConnection(pika.URLParameters(TEST_RABBITMQ_URL))
    ch = connection.channel()
    declare_topology(ch)
    ch.queue_purge(QUEUE_NAME)
    ch.queue_purge(DLQ_NAME)
    try:
        yield ch
    finally:
        ch.queue_purge(QUEUE_NAME)
        ch.queue_purge(DLQ_NAME)
        connection.close()


def _settings() -> Settings:
    assert TEST_RABBITMQ_URL is not None
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url=TEST_RABBITMQ_URL,
        github_webhook_secret="test-secret",
    )


def _count(ch: pika.adapters.blocking_connection.BlockingChannel, queue: str) -> int:
    count = ch.queue_declare(queue, passive=True).method.message_count
    assert count is not None
    return count


def test_two_jobs_at_once_run_in_parallel_on_two_workers(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    queue = RabbitMQJobQueue(TEST_RABBITMQ_URL)  # type: ignore[arg-type]
    queue.enqueue(JOB)
    queue.enqueue(replace(JOB, id=UUID(int=31), review_run_id=UUID(int=32)))

    barrier = threading.Barrier(2, timeout=BARRIER_TIMEOUT_SECONDS)
    handled: dict[str, bytes] = {}
    lock = threading.Lock()

    def worker(name: str) -> None:
        def handle(body: bytes) -> None:
            barrier.wait()
            with lock:
                handled[name] = body

        build_worker(_settings()).run(handle, limit=1)

    threads = [
        threading.Thread(target=worker, args=(name,), name=name) for name in ("dmc268-worker", "dmc268-worker-2")
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=JOIN_TIMEOUT_SECONDS)

    assert not any(thread.is_alive() for thread in threads), "воркер не обработал задачу"
    assert not barrier.broken, "задачи шли не одновременно: второй обработчик не дошёл до барьера"
    # Каждый воркер взял по задаче, и задачи разные.
    assert set(handled) == {"dmc268-worker", "dmc268-worker-2"}
    assert len(set(handled.values())) == 2
    # Обе подтверждены: не вернулись в очередь и не ушли в DLQ.
    assert _count(channel, QUEUE_NAME) == 0
    assert _count(channel, DLQ_NAME) == 0
