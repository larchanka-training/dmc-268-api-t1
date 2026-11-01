"""Интеграционный тест адаптера `JobQueue`. Нужен живой RabbitMQ."""

import dataclasses
import json
import time
from collections.abc import Iterator
from uuid import UUID

import pika
import pytest
from pika.exceptions import ChannelClosedByBroker

from app.domain.entities import ReviewJob
from app.infrastructure.queue.rabbitmq import (
    CONSUMER_ARGUMENTS,
    CONSUMER_TIMEOUT_SECONDS,
    DEFAULT_PRIORITY,
    DELIVERY_LIMIT,
    DLQ_NAME,
    DLX_NAME,
    QUEUE_NAME,
    SIMULATION_PRIORITY,
    RabbitMQJobQueue,
    declare_topology,
)

from ..conftest import TEST_RABBITMQ_URL, requires_broker

pytestmark = [pytest.mark.integration, requires_broker]

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


def test_enqueue_publishes_the_wire_message_with_default_priority(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    assert TEST_RABBITMQ_URL is not None
    RabbitMQJobQueue(TEST_RABBITMQ_URL).enqueue(JOB)

    method, properties, body = channel.basic_get(QUEUE_NAME, auto_ack=True)
    assert method is not None
    assert body is not None
    assert json.loads(body) == {
        "job_id": str(JOB.id),
        "review_run_id": str(JOB.review_run_id),
        "event_type": "pull_request",
        "action": "opened",
        "installation_id": 512804923,
        "repository": {"id": "987654", "full_name": "owner/repo"},
        "pull_request": {"number": 42, "head_sha": "abc123def", "base_sha": "fed654cba"},
    }
    assert properties is not None
    assert properties.priority == DEFAULT_PRIORITY


def test_nacked_message_lands_in_the_dead_letter_queue(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    assert TEST_RABBITMQ_URL is not None
    RabbitMQJobQueue(TEST_RABBITMQ_URL).enqueue(JOB)

    method, _, _ = channel.basic_get(QUEUE_NAME, auto_ack=False)
    assert method is not None
    assert method.delivery_tag is not None
    channel.basic_nack(method.delivery_tag, requeue=False)

    # Dead-lettering — асинхронная операция брокера: сообщение не появляется
    # в DLQ мгновенно в рамках того же `basic_nack`, поэтому опрашиваем
    # вместо одного немедленного `basic_get`.
    dlq_method = None
    dlq_body: str | None = None
    for _ in range(20):
        dlq_method, _, dlq_body = channel.basic_get(DLQ_NAME, auto_ack=True)
        if dlq_method is not None:
            break
        time.sleep(0.05)

    assert dlq_method is not None
    assert dlq_body is not None
    assert json.loads(dlq_body)["job_id"] == str(JOB.id)


def test_the_queue_is_redeclared_without_a_consumer_timeout(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    """Таймаут подтверждения - не аргумент очереди: его правка не ломает объявление.

    Повторное объявление с другими аргументами брокер отвергает (`406`), поэтому
    аргумент очереди со значением таймаута уронил бы `declare_topology` у воркера и
    API после первой же правки `CONSUMER_TIMEOUT_SECONDS`."""
    probe = channel.connection.channel()
    with pytest.raises(ChannelClosedByBroker) as rejected:
        probe.queue_declare(
            QUEUE_NAME,
            durable=True,
            arguments={
                "x-queue-type": "quorum",
                "x-delivery-limit": DELIVERY_LIMIT,
                "x-dead-letter-exchange": DLX_NAME,
                "x-consumer-timeout": CONSUMER_TIMEOUT_SECONDS * 1000,
            },
        )
    assert rejected.value.reply_code == 406
    declare_topology(channel)


def test_the_broker_takes_a_message_from_a_consumer_past_its_timeout(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    """Quorum-очередь применяет `x-consumer-timeout` аргумента потребителя (#223).

    Брокер проверяет таймауты раз в несколько секунд, поэтому ждём с запасом."""
    RabbitMQJobQueue(TEST_RABBITMQ_URL or "").enqueue(JOB)
    deliveries = channel.consume(
        QUEUE_NAME, inactivity_timeout=1, arguments={"x-consumer-timeout": 1000}
    )
    method, _, _ = next(deliveries)
    assert method is not None

    deadline = time.monotonic() + 60
    for method, _, _ in deliveries:
        assert method is None, "после отмены брокер ничего не выдаёт"
        assert time.monotonic() < deadline, "брокер не снял потребителя по таймауту"
    # Генератор кончился: брокер прислал `basic.cancel`, сообщение вернулось.
    assert channel.queue_declare(QUEUE_NAME, passive=True).method.message_count == 1


def _review(n: int) -> ReviewJob:
    return dataclasses.replace(JOB, id=UUID(int=100 + n))


def _publish_simulation(
    channel: pika.adapters.blocking_connection.BlockingChannel, n: int
) -> None:
    """Задача симуляции так, как её поставит API: приоритет ниже ревью.

    Отдельный канал с подтверждением публикации: к возврату сообщение уже в
    очереди, и следующая постановка встаёт за ним."""
    publisher = channel.connection.channel()
    publisher.confirm_delivery()
    publisher.basic_publish(
        exchange="",
        routing_key=QUEUE_NAME,
        body=json.dumps({"job_id": str(UUID(int=200 + n)), "simulation_id": str(UUID(int=n))}),
        properties=pika.BasicProperties(
            content_type="application/json",
            delivery_mode=pika.spec.PERSISTENT_DELIVERY_MODE,
            priority=SIMULATION_PRIORITY,
            type="simulation",
        ),
        mandatory=True,
    )
    publisher.close()


def _consume_like_the_worker(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> Iterator[tuple[int, str, int | None]]:
    """Потребитель с настройками воркера (`build_worker`): prefetch 1, аргументы потребителя.

    Отдаёт тег доставки, `job_id` и приоритет сообщения; подтверждает вызывающий."""
    channel.basic_qos(prefetch_count=1)
    for method, properties, body in channel.consume(
        QUEUE_NAME, inactivity_timeout=5, arguments=CONSUMER_ARGUMENTS
    ):
        assert method is not None, "брокер не выдал ждущее сообщение"
        assert method.delivery_tag is not None
        assert properties is not None
        assert body is not None
        yield method.delivery_tag, json.loads(body)["job_id"], properties.priority


def test_a_review_enqueued_after_a_waiting_simulation_is_taken_first(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    """Воркер занят, в очереди ждёт симуляция, следом приходит ревью: следующим он берёт ревью (D2)."""
    queue = RabbitMQJobQueue(TEST_RABBITMQ_URL or "")
    queue.enqueue(_review(1))
    deliveries = _consume_like_the_worker(channel)
    busy_tag, busy_job, _ = next(deliveries)
    assert busy_job == str(_review(1).id)

    _publish_simulation(channel, 1)
    queue.enqueue(_review(2))
    channel.basic_ack(busy_tag)

    tag, job, priority = next(deliveries)
    assert (job, priority) == (str(_review(2).id), DEFAULT_PRIORITY)
    channel.basic_ack(tag)
    tag, job, priority = next(deliveries)
    assert (job, priority) == (str(UUID(int=201)), SIMULATION_PRIORITY)
    channel.basic_ack(tag)
    channel.cancel()


def test_every_waiting_review_goes_before_any_waiting_simulation(
    channel: pika.adapters.blocking_connection.BlockingChannel,
) -> None:
    """Приоритет строгий: симуляции не получают долю выдачи, пока ждёт хоть одно ревью.

    На этом стоит оценка задержки ревью в D2; внутри одного приоритета - FIFO.
    Если обновление образа брокера поменяет порядок, тест упадёт раньше стенда."""
    queue = RabbitMQJobQueue(TEST_RABBITMQ_URL or "")
    for n in range(3):
        _publish_simulation(channel, n)
    for n in range(3):
        queue.enqueue(_review(n))

    order = []
    deliveries = _consume_like_the_worker(channel)
    for _ in range(6):
        tag, job, _ = next(deliveries)
        order.append(job)
        channel.basic_ack(tag)
    channel.cancel()

    reviews = [str(_review(n).id) for n in range(3)]
    simulations = [str(UUID(int=200 + n)) for n in range(3)]
    assert order == reviews + simulations
