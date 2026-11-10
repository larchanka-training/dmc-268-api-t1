"""Решение о судьбе сообщения после попытки обработки (#157).

Чистая функция: получает исключение (или его отсутствие) аргументом и не трогает
канал сама — вызывающий код в `factory.py` переводит решение в вызов брокера. Типов
pika здесь нет: решение не зависит от протокола брокера.
"""

from enum import Enum

from app.application.review_pipeline import ReviewNotStarted


class Decision(Enum):
    # Обработано — подтвердить.
    ACK = "ack"
    # Прогон не начат из-за временного сбоя — вернуть в очередь. Повтор тратит попытку
    # доставки: после лимита (`DELIVERY_LIMIT`) брокер сам уведёт сообщение в DLQ.
    RETRY = "retry"
    # Сбой после захвата прогона или битое сообщение — в DLQ для разбора.
    DEAD_LETTER = "dead_letter"


def decide(exc: BaseException | None) -> Decision:
    if exc is None:
        return Decision.ACK
    if isinstance(exc, ReviewNotStarted):
        return Decision.RETRY
    return Decision.DEAD_LETTER
