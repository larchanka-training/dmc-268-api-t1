"""Фейковые канал и соединение pika для consume-цикла воркера (`Worker.run`).

Отдают заранее готовые доставки `(method, properties, body)` и запоминают
ack/nack. Без брокера.
"""

from collections.abc import Iterator, Sequence
from typing import Any


class FakeChannel:
    """Канал вместо pika.

    `allow_nack=False` - nack роняет тест: в нём сообщение не должно уйти в
    DLQ. `current` - номер (с нуля) доставки, которую цикл сейчас обрабатывает.
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
        self.cancelled = False
        # Номер доставки, которую цикл сейчас обрабатывает; `None` - ещё ни одной.
        self.current: int | None = None

    def consume(self, queue: str, inactivity_timeout: float) -> Iterator[Any]:
        for index, delivery in enumerate(self._deliveries):
            self.current = index
            yield delivery

    def basic_ack(self, tag: int) -> None:
        self.acked.append(tag)

    def basic_nack(self, tag: int, requeue: bool) -> None:
        if not self._allow_nack:
            raise AssertionError("не ожидался nack")
        self.nacked.append(tag)

    def cancel(self) -> None:
        self.cancelled = True


class FakeConnection:
    """Соединение вместо pika: помнит, что его закрыли."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True
