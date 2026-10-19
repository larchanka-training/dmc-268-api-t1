"""Поток событий статуса прогона: что отправить.

Функция чистая. Время приходит числом секунд, а не читается с часов,
поэтому «пятнадцать секунд тишины» тест проверяет без ожидания. Цикл, который
её зовёт, живёт в `app/application/review_run_events.py`, запись события в текст
SSE — `format_sse` в `app/api/review_events.py`.
"""

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.entities import ReviewRun
from app.domain.enums import TERMINAL_STATUSES, ReviewRunStatus

# Обещано не реже раза в 15 секунд (клиент считает поток мёртвым после 45).
# Секунда запаса — на шаг опроса и чтение базы: тишина меряется шагами цикла.
PING_AFTER_SECONDS = 14.0


@dataclass(frozen=True, slots=True)
class StatusEvent:
    """Состояние прогона: и снимок при подключении, и смена статуса."""

    review_run_id: UUID
    status: ReviewRunStatus
    last_progress_at: datetime
    failed_stage: ReviewRunStatus | None
    failure_reason: str | None


@dataclass(frozen=True, slots=True)
class PingEvent:
    """Признак жизни потока; данных не несёт."""


type RunEvent = StatusEvent | PingEvent


@dataclass(frozen=True, slots=True)
class Decision:
    """Что отправить на этом шаге и закрыть ли после этого поток."""

    event: RunEvent | None
    close: bool


def next_event(
    run: ReviewRun, *, last_sent: ReviewRunStatus | None, idle_seconds: float
) -> Decision:
    """Решить, что уходит клиенту.

    `last_sent` — статус из последнего отправленного события, `None` до
    первого: тогда уходит снимок, каким бы статус ни был. `idle_seconds` —
    сколько прошло с последнего отправленного события любого вида.
    """
    if run.status != last_sent:
        event = StatusEvent(
            review_run_id=run.id,
            status=run.status,
            last_progress_at=run.last_progress_at,
            failed_stage=run.failed_stage,
            failure_reason=run.failure_reason,
        )
        return Decision(event=event, close=run.status in TERMINAL_STATUSES)
    if idle_seconds >= PING_AFTER_SECONDS:
        return Decision(event=PingEvent(), close=False)
    return Decision(event=None, close=False)
