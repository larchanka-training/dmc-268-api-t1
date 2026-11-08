"""Удалён ли дифф прогона по сроку хранения (change `diff-retention-visibility`, D1).

Воркер стирает тела пакетов контекста через `CONTEXT_PAYLOAD_RETENTION_DAYS`. Дифф
прогона считается удалённым, когда стёрты тела всех его пакетов; частично стёртый
прогон ещё живой. Прогон без пакетов (упал до сборки контекста) не удалён: стирать
было нечего.

Агрегат считает адаптер одним запросом без чтения тел, решение — эта функция.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BodyExpiryStats:
    """Агрегат тел пакетов контекста одного прогона."""

    payload_count: int
    erased_count: int
    # Самое позднее `body_expired_at` среди стёртых; `None`, если стёртых нет.
    last_expired_at: datetime | None


def diff_expired_at(stats: BodyExpiryStats) -> datetime | None:
    """Время удаления диффа прогона или `None`, если дифф не удалён."""
    if stats.payload_count == 0 or stats.erased_count < stats.payload_count:
        return None
    return stats.last_expired_at
