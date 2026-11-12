"""Решение о судьбе сообщения после попытки обработки (#157).

Чистая функция: получает исключение (или его отсутствие) аргументом и не трогает
канал сама — вызывающий код в `factory.py` переводит решение в вызов брокера. Типов
pika здесь нет: решение не зависит от протокола брокера.
"""

from enum import Enum

from app.application.review_pipeline import ReviewDeferred, ReviewNotStarted


class Decision(Enum):
    # Обработано — подтвердить.
    ACK = "ack"
    # Прогон не начат из-за временного сбоя — вернуть в очередь. Повтор тратит попытку
    # доставки: после лимита (`DELIVERY_LIMIT`) брокер сам уведёт сообщение в DLQ.
    RETRY = "retry"
    # Провайдер на паузе breaker'а (#294) — вернуть в очередь, не тратя попытку доставки:
    # пауза может длиться дольше всех `DELIVERY_LIMIT` повторов.
    DEFER = "defer"
    # Сбой после захвата прогона или битое сообщение — в DLQ для разбора.
    DEAD_LETTER = "dead_letter"


def decide(exc: BaseException | None) -> Decision:
    if exc is None:
        return Decision.ACK
    if isinstance(exc, ReviewDeferred):
        return Decision.DEFER
    if isinstance(exc, ReviewNotStarted):
        return Decision.RETRY
    return Decision.DEAD_LETTER
