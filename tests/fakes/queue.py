"""Фейковые канал и соединение pika для consume-цикла воркера (`Worker.run`).

Отдают заранее готовые доставки `(method, properties, body)` и запоминают
ack/nack. Без брокера.
"""

from collections.abc import Iterator, Sequence
from typing import Any

from pika.exceptions import ChannelClosedByBroker


class FakeChannel:
    """Канал вместо pika.

    `allow_nack=False` - nack в DLQ роняет тест: в нём сообщение не должно уйти в
    DLQ; возврат в очередь (`requeue`) - штатная остановка, он разрешён всегда.
    `current` - номер (с нуля) доставки, которую цикл сейчас обрабатывает.
    """

    def __init__(
        self,
        deliveries: Sequence[tuple[Any, Any, Any]],
        *,
        allow_nack: bool = True,
    ) -> None:
        self._deliveries = list(deliveries)
        self._allow_nack = allow_nack
        self.acked: list[int] = []
        self.nacked: list[int] = []
        self.requeued: list[int] = []
        self.cancelled = False
        # Номер доставки, которую цикл сейчас обрабатывает; `None` - ещё ни одной.
        self.current: int | None = None
        # Порядок `cancel` и подтверждений: с prefetch=1 `cancel` после
        # подтверждения отдал бы следующее сообщение `basic.reject` (#223).
        self.events: list[str] = []
        self.consumer_arguments: dict[str, Any] | None = None

    def consume(
        self, queue: str, inactivity_timeout: float, arguments: dict[str, Any] | None = None
    ) -> Iterator[Any]:
        self.consumer_arguments = arguments
        for index, delivery in enumerate(self._deliveries):
            # Снятый потребитель новых доставок не получает.
            if self.cancelled:
                return
            self.current = index
            yield delivery

    def basic_ack(self, tag: int) -> None:
        self.acked.append(tag)
        self.events.append(f"ack {tag}")

    def basic_nack(self, tag: int, requeue: bool) -> None:
        if requeue:
            self.requeued.append(tag)
            self.events.append(f"requeue {tag}")
            return
        if not self._allow_nack:
            raise AssertionError("не ожидался nack в DLQ")
        self.nacked.append(tag)

    def cancel(self) -> None:
        assert not self.cancelled, "потребитель снят дважды"
        self.cancelled = True
        self.events.append("cancel")


class NoLegacyQueueChannel:
    """Канал брокера, где старой классической очереди нет: пассивное объявление — `404`."""

    is_open = False

    def queue_declare(self, queue: str, passive: bool) -> None:
        raise ChannelClosedByBroker(404, f"NOT_FOUND - no queue '{queue}'")


class FakeConnection:
    """Соединение вместо pika: помнит, что его закрыли; старой очереди у брокера нет."""

    def __init__(self) -> None:
        self.closed = False
        self.channels_opened = 0

    def channel(self) -> NoLegacyQueueChannel:
        self.channels_opened += 1
        return NoLegacyQueueChannel()

    def close(self) -> None:
        self.closed = True
