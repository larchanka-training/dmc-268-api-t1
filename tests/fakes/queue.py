"""Фейковые канал и соединение pika для consume-цикла воркера (`Worker.run`).

Отдают заранее готовые доставки `(method, properties, body)` и запоминают
ack/nack. Без брокера.
"""

import time
from collections.abc import Iterator, Sequence
from typing import Any


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
        # Возвращённые на повтор (`basic_reject` с requeue): попытка доставки тратится.
        self.rejected: list[int] = []
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
            self.events.append(f"requeue {tag}")
            return
        if not self._allow_nack:
            raise AssertionError("не ожидался nack в DLQ")
        self.nacked.append(tag)

    def basic_reject(self, tag: int, requeue: bool) -> None:
        assert requeue, "reject без requeue воркер не шлёт: в DLQ уводит nack"
        self.rejected.append(tag)
        self.events.append(f"reject {tag}")

    def cancel(self) -> None:
        assert not self.cancelled, "потребитель снят дважды"
        self.cancelled = True
        self.events.append("cancel")


class FakeConnection:
    """Соединение вместо pika: помнит, что его закрыли."""

    def __init__(self) -> None:
        self.closed = False
        # Сколько раз цикл соединения крутился, пока шла обработка: это и ответ на heartbeat.
        self.polls = 0
        # Шаги паузы циклом соединения (`connection.sleep`) — перед возвратом сообщения.
        self.slept: list[float] = []

    def process_data_events(self, time_limit: float = 0) -> None:
        self.polls += 1
        time.sleep(min(time_limit, 0.01))

    def sleep(self, duration: float) -> None:
        self.slept.append(duration)

    def close(self) -> None:
        self.closed = True
