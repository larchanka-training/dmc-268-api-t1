"""Поток событий статуса прогона: что отправить и как это записать.

Обе функции чистые. Время приходит числом секунд, а не читается с часов,
поэтому «пятнадцать секунд тишины» тест проверяет без ожидания. Цикл, который
их зовёт, живёт в `app/application/review_run_events.py`.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
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


def format_sse(event: RunEvent) -> str:
    """Записать событие в формате Server-Sent Events.

    Именованное событие, а не комментарий: комментарии `EventSource` до кода
    клиента не доводит. JSON — в одну строку: перенос внутри `data` разбил бы
    событие на несколько полей.
    """
    if isinstance(event, PingEvent):
        return "event: ping\ndata: {}\n\n"
    data = {
        "review_run_id": str(event.review_run_id),
        "status": event.status.value,
        "last_progress_at": _iso_utc(event.last_progress_at),
        "failed_stage": event.failed_stage.value if event.failed_stage else None,
        "failure_reason": event.failure_reason,
    }
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: status\ndata: {body}\n\n"


def _iso_utc(moment: datetime) -> str:
    # База отдаёт время в зоне сессии; клиенту оно уходит в UTC с `Z`, как в
    # остальных ответах API.
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
