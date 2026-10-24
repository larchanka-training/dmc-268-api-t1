"""Адаптер `JobQueue` на RabbitMQ.

Тонкий: `to_wire_message`/`from_wire_message` — чистый перевод сущности в
формат из `docs/SYSTEM_DESIGN.md` §4.2 и обратно, `RabbitMQJobQueue` —
только транспорт и топология (декларация очереди/exchange'ей идемпотентна на
уровне AMQP, это не бизнес-правило). `from_wire_message` использует воркер
(`app/worker/factory.py`), чтобы разобрать сообщение, полученное из очереди,
обратно в `ReviewJob`.

Контекст трассировки (W3C `traceparent`) идёт в заголовках AMQP, а не в теле:
тело — только доменные данные. Обе стороны — здесь: `_publish` кладёт контекст
спана публикации, `consuming_span` воркера достаёт его и продолжает трейс.
Заголовкам нашей очереди доверяем: писать в неё может только API. Автоинструментирование
pika не берём: для генератора `channel.consume()` оно закрывает спан приёма сразу и
держит его контекст текущим до следующего сообщения, то есть и на простое цикла.
"""

import contextlib
import json
import threading
from collections.abc import Iterator
from typing import Any
from uuid import UUID

import pika
from opentelemetry import trace
from opentelemetry.trace import SpanKind
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pika import spec
from pika.exceptions import (
    AMQPConnectionError,
    ChannelWrongStateError,
    ConnectionBlockedTimeout,
)
from pika.exchange_type import ExchangeType

from app.domain.entities import ReviewJob
from app.infrastructure.telemetry import REVIEW_RUN_ID, error_status_span

QUEUE_NAME = "review_jobs"
DLX_NAME = "review_jobs.dlx"
DLQ_NAME = "review_jobs.dlq"
MAX_PRIORITY = 9
# Приоритет должен был бы зависеть от тарифа аккаунта, но `accounts` ещё не
# реализован (см. BACKEND_ARCHITECTURE.md, "Спроектированы, но не
# реализованы") — фиксированный дефолт до тех пор, см. design.md
# change'а add-rabbitmq-job-queue.
DEFAULT_PRIORITY = 5

# При алярме памяти или диска брокер перестаёт читать сокет издателя, и
# подтверждение публикации не приходит, пока алярм не снят. Оба таймаута держат
# худший `enqueue` (ожидание lock'а + блокировка) под 10-секундным лимитом
# доставки вебхука GitHub: отказ → прогон в failed → 5xx. Сам GitHub упавшую
# доставку не повторяет — её переотправляет задача #149.
BLOCKED_CONNECTION_TIMEOUT_SECONDS = 3
LOCK_TIMEOUT_SECONDS = 3

# Явный пропагатор, а не глобальный: глобальный пуст, чтобы не доверять
# `traceparent` HTTP-клиентов (`app/infrastructure/telemetry.py`).
_PROPAGATOR = TraceContextTextMapPropagator()
_tracer = trace.get_tracer(__name__)

# Атрибуты спанов очереди — semconv messaging для RabbitMQ (статус Development).
# Публикуем в default exchange с routing key = имя очереди: по semconv пустой
# exchange опускается, а у потребителя совпавшие routing key и очередь пишутся
# один раз, поэтому `messaging.destination.name` с обеих сторон — имя очереди.
_MESSAGING_ATTRIBUTES = {
    "messaging.system": "rabbitmq",
    "messaging.destination.name": QUEUE_NAME,
    "messaging.rabbitmq.destination.routing_key": QUEUE_NAME,
}


def to_wire_message(job: ReviewJob) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "review_run_id": str(job.review_run_id),
        "event_type": job.event_type,
        "action": job.action,
        "installation_id": job.installation_id,
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
        review_run_id=UUID(message["review_run_id"]),
        event_type=message["event_type"],
        action=message["action"],
        installation_id=message["installation_id"],
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


@contextlib.contextmanager
def consuming_span(
    properties: spec.BasicProperties | None, delivery_tag: int
) -> Iterator[None]:
    """Спан обработки сообщения воркером — продолжение трейса, начатого при публикации.

    Контекст сообщения — родитель спана, а не link: сообщение обрабатывается одно, и
    спан обработки корневой в воркере, semconv это допускает для одиночного сообщения.
    Если трейсы «запрос → прогон» станут неудобно длинными — перейти на link.

    Нет заголовка `traceparent` (сообщение старше трассировки, его переложили руками)
    или он битый — спан начинает новый трейс, обработка идёт как обычно. Исключение
    обработки выходит наружу через контекст-менеджер: спан записывает его событием
    `exception` и закрывается со статусом ошибки, в описании — только тип
    (`error_status_span`). `KeyboardInterrupt` и `SystemExit` — тоже.
    """
    headers = (properties.headers if properties is not None else None) or {}
    # pika отдаёт строковый заголовок `str`, а не декодируемый в UTF-8 — `bytes`.
    carrier = {
        key: value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        for key, value in headers.items()
        if isinstance(value, str | bytes)
    }
    with error_status_span(
        _tracer,
        f"process {QUEUE_NAME}",
        context=_PROPAGATOR.extract(carrier),
        kind=SpanKind.CONSUMER,
        attributes={
            **_MESSAGING_ATTRIBUTES,
            "messaging.operation.name": "process",
            "messaging.operation.type": "process",
            "messaging.rabbitmq.message.delivery_tag": delivery_tag,
        },
    ):
        yield


class PublisherBlockedError(RuntimeError):
    """Брокер блокирует издателей (алярм памяти или диска): задача не поставлена."""


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
        # Канал закрывается брокером независимо от соединения (channel-level
        # error: unroutable при `mandatory`, nack, несовпадающий queue_declare),
        # поэтому проверяется и он: иначе живое соединение отдавало бы мёртвый
        # канал, и каждый следующий `enqueue` падал бы до перезапуска процесса.
        if (
            self._connection is None
            or self._connection.is_closed
            or self._channel is None
            or self._channel.is_closed
        ):
            parameters = pika.URLParameters(self._url)
            # Заблокированное брокером дольше таймаута соединение pika рвёт и
            # поднимает `ConnectionBlockedTimeout`; без таймаута ждёт вечно.
            parameters.blocked_connection_timeout = BLOCKED_CONNECTION_TIMEOUT_SECONDS
            self._connection = pika.BlockingConnection(parameters)
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

    def _drop_connection(self) -> None:
        # Соединение уже мертво или подозрительно: закрыть без шума и забыть,
        # `_channel_ready` откроет новое.
        if self._connection is not None:
            with contextlib.suppress(Exception):  # закрываем заведомо сломанное соединение
                self._connection.close()
        self._connection = None
        self._channel = None

    def _publish(self, job: ReviewJob, headers: dict[str, str]) -> None:
        try:
            self._basic_publish(job, headers)
        except ConnectionBlockedTimeout as exc:
            # Блокировка — не обрыв: повтор на новом соединении упрётся в тот же
            # алярм. `PublisherBlockedError` не `AMQPConnectionError`, поэтому
            # повтор в `enqueue` её не ловит. Соединение pika уже порвал.
            self._drop_connection()
            raise PublisherBlockedError(
                f"брокер блокирует издателей дольше {BLOCKED_CONNECTION_TIMEOUT_SECONDS} с "
                "(алярм памяти или диска)"
            ) from exc

    def _basic_publish(self, job: ReviewJob, headers: dict[str, str]) -> None:
        self._channel_ready().basic_publish(
            exchange="",
            routing_key=QUEUE_NAME,
            body=json.dumps(to_wire_message(job)).encode("utf-8"),
            properties=pika.BasicProperties(
                content_type="application/json",
                delivery_mode=spec.PERSISTENT_DELIVERY_MODE,
                priority=DEFAULT_PRIORITY,
                headers=headers,
            ),
            mandatory=True,
        )

    def enqueue(self, job: ReviewJob) -> None:
        # `BlockingConnection` шлёт heartbeat, только пока по нему что-то идёт:
        # после простоя без вебхуков брокер закрывает соединение, а `is_closed`
        # узнаёт об этом лишь при следующей записи. GitHub доставку не
        # повторяет, поэтому обрыв соединения переживаем здесь: переподключаемся
        # и публикуем ещё раз, один раз. `UnroutableError`/`NackError` — ответ
        # брокера, а не обрыв, и сюда не попадают.
        # Спан — одна публикация с точки зрения вызывающего, вместе с повтором:
        # обрыв, который пережил повтор, не ошибка, и ERROR спан получает, только
        # если упала и вторая попытка.
        # Lock — с таймаутом: пока одна постановка ждёт заблокированного брокера,
        # остальные не должны копиться за ней и съедать threadpool. Соединение
        # при этом не трогаем — им владеет другой поток.
        if not self._lock.acquire(timeout=LOCK_TIMEOUT_SECONDS):
            raise PublisherBlockedError(
                f"брокер блокирует издателей: соединение занято другой постановкой "
                f"дольше {LOCK_TIMEOUT_SECONDS} с"
            )
        try:
            self._publish_with_retry(job)
        finally:
            self._lock.release()

    def _publish_with_retry(self, job: ReviewJob) -> None:
        with _tracer.start_as_current_span(
            f"publish {QUEUE_NAME}",
            kind=SpanKind.PRODUCER,
            attributes={
                **_MESSAGING_ATTRIBUTES,
                "messaging.operation.name": "publish",
                "messaging.operation.type": "send",
                REVIEW_RUN_ID: str(job.review_run_id),
            },
        ):
            headers: dict[str, str] = {}
            _PROPAGATOR.inject(headers)
            try:
                self._publish(job, headers)
            except (AMQPConnectionError, ChannelWrongStateError):
                self._drop_connection()
                self._publish(job, headers)
