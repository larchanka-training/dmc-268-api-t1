"""Постановка в очередь при алярме брокера. Нужны живой RabbitMQ и его контейнер.

Алярм памяти включается `rabbitmqctl` внутри контейнера брокера: другого способа
у теста нет, management API в образе `alpine` не включён. В CI контейнер — service
`rabbitmq`, его идентификатор CI кладёт в `TEST_RABBITMQ_CONTAINER`.
"""

import os
import subprocess
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, wait
from uuid import UUID

import pika
import pytest

from app.domain.entities import ReviewJob
from app.infrastructure.queue.rabbitmq import (
    QUEUE_NAME,
    PublisherBlockedError,
    RabbitMQJobQueue,
    declare_topology,
)

from ..conftest import TEST_RABBITMQ_URL, requires_broker

TEST_RABBITMQ_CONTAINER = os.environ.get("TEST_RABBITMQ_CONTAINER")

pytestmark = [
    pytest.mark.integration,
    requires_broker,
    pytest.mark.skipif(
        not TEST_RABBITMQ_CONTAINER,
        reason="needs the RabbitMQ container to raise an alarm; set TEST_RABBITMQ_CONTAINER",
    ),
]

JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    event_type="pull_request",
    action="opened",
    installation_id=512804923,
    repository_provider_id="987654",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="abc123def",
    base_sha="fed654cba",
)

# Лимит доставки вебхука у GitHub — 10 с: отказ должен успеть раньше.
WEBHOOK_DEADLINE_SECONDS = 10


def _rabbitmqctl(*args: str, check: bool = True) -> int:
    assert TEST_RABBITMQ_CONTAINER is not None
    return subprocess.run(
        ["docker", "exec", TEST_RABBITMQ_CONTAINER, *args],
        check=check,
        capture_output=True,
        timeout=60,
    ).returncode


def _wait_alarm(active: bool) -> None:
    # `check_alarms` возвращает ненулевой код, пока алярм есть.
    for _ in range(60):
        has_alarm = _rabbitmqctl("rabbitmq-diagnostics", "-q", "check_alarms", check=False) != 0
        if has_alarm == active:
            return
        time.sleep(0.5)
    raise AssertionError(f"алярм брокера не перешёл в состояние active={active}")


@pytest.fixture
def memory_alarm() -> Iterator[None]:
    _rabbitmqctl("rabbitmqctl", "set_vm_memory_high_watermark", "absolute", "1MiB")
    _wait_alarm(active=True)
    try:
        yield
    finally:
        # Алярм глобален для брокера: не снять его — повесить соседние тесты.
        _rabbitmqctl("rabbitmqctl", "set_vm_memory_high_watermark", "0.6")
        _wait_alarm(active=False)


def _timed_enqueue(queue: RabbitMQJobQueue) -> tuple[float, PublisherBlockedError | None]:
    started = time.monotonic()
    try:
        queue.enqueue(JOB)
    except PublisherBlockedError as exc:
        return time.monotonic() - started, exc
    return time.monotonic() - started, None


def test_enqueue_fails_fast_while_the_broker_blocks_publishers(memory_alarm) -> None:
    assert TEST_RABBITMQ_URL is not None
    queue = RabbitMQJobQueue(TEST_RABBITMQ_URL)
    pool = ThreadPoolExecutor(max_workers=3)
    try:
        futures = [pool.submit(_timed_enqueue, queue) for _ in range(3)]
        done, not_done = wait(futures, timeout=WEBHOOK_DEADLINE_SECONDS + 5)
        assert not not_done, "enqueue висит, пока брокер блокирует издателей"
        for elapsed, error in (future.result() for future in done):
            assert isinstance(error, PublisherBlockedError)
            assert "блокирует издателей" in str(error)
            assert elapsed < WEBHOOK_DEADLINE_SECONDS
    finally:
        # Зависшие потоки отпустит снятие алярма в фикстуре.
        pool.shutdown(wait=False)


def test_enqueue_recovers_after_the_alarm_clears() -> None:
    assert TEST_RABBITMQ_URL is not None
    queue = RabbitMQJobQueue(TEST_RABBITMQ_URL)
    connection = pika.BlockingConnection(pika.URLParameters(TEST_RABBITMQ_URL))
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        channel = connection.channel()
        declare_topology(channel)
        channel.queue_purge(QUEUE_NAME)
        queue.enqueue(JOB)  # соединение открыто до алярма, как у живого API
        channel.queue_purge(QUEUE_NAME)
        _rabbitmqctl("rabbitmqctl", "set_vm_memory_high_watermark", "absolute", "1MiB")
        try:
            _wait_alarm(active=True)
            blocked = pool.submit(_timed_enqueue, queue)
            _, error = blocked.result(timeout=WEBHOOK_DEADLINE_SECONDS + 5)
            assert isinstance(error, PublisherBlockedError)
        finally:
            _rabbitmqctl("rabbitmqctl", "set_vm_memory_high_watermark", "0.6")
            _wait_alarm(active=False)

        queue.enqueue(JOB)

        method, _, _ = channel.basic_get(QUEUE_NAME, auto_ack=True)
        assert method is not None
    finally:
        pool.shutdown(wait=False)
        connection.close()
