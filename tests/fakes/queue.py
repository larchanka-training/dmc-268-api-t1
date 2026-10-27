"""Фейковые канал и соединение pika для consume-цикла воркера (`Worker.run`).

Отдают заранее готовые доставки `(method, properties, body)` и запоминают
ack/nack. Без брокера.
"""

from collections.abc import Iterator, Mapping, Sequence
from typing import Any


class FakeChannel:
    """Канал вместо pika.

    `allow_nack=False` - nack роняет тест: в нём сообщение не должно уйти в
    DLQ. `redelivery_of` - номер доставки (с нуля) -> номер исходной: такая
    доставка повторяет исходную, как брокер после потерянного ack, и
    приходит, только если исходная получила ack. После nack (`requeue=False`)
    сообщение уже в DLQ, повтора нет: номер пропущенной доставки - в `skipped`.
    """

    def __init__(
        self,
        deliveries: Sequence[tuple[Any, Any, Any]],
        *,
        allow_nack: bool = True,
        redelivery_of: Mapping[int, int] | None = None,
    ) -> None:
        self._deliveries = list(deliveries)
        self._allow_nack = allow_nack
        self._redelivery_of = dict(redelivery_of or {})
        self.acked: list[int] = []
        self.nacked: list[int] = []
        self.skipped: list[int] = []
        self.cancelled = False
        # Номер доставки, которую цикл сейчас обрабатывает; `None` - ещё ни одной.
        self.current: int | None = None

    def consume(self, queue: str, inactivity_timeout: float) -> Iterator[Any]:
        for index, delivery in enumerate(self._deliveries):
            original = self._redelivery_of.get(index)
            if original is not None and not self._was_acked(original):
                self.skipped.append(index)
                continue
            self.current = index
            yield delivery

    def _was_acked(self, index: int) -> bool:
        method = self._deliveries[index][0]
        return method is not None and method.delivery_tag in self.acked

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
