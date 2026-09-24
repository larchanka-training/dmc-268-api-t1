"""Машина состояний прогона ревью.

Одна таблица переходов, к которой обращается всё, что пишет статус. Прогон,
дошедший до терминального состояния, больше ничего не принимает — именно это
не даёт повторно доставленному сообщению из очереди перезапустить завершённую
работу.
"""

from dataclasses import replace
from datetime import datetime
from types import MappingProxyType

from app.domain.entities import ReviewRun
from app.domain.enums import TERMINAL_STATUSES, ReviewRunStatus
from app.domain.result import Result

S = ReviewRunStatus

_ALLOWED: MappingProxyType[ReviewRunStatus, frozenset[ReviewRunStatus]] = MappingProxyType(
    {
        S.QUEUED: frozenset({S.BUILDING_CONTEXT, S.FAILED, S.CANCELLED}),
        S.BUILDING_CONTEXT: frozenset({S.ANALYSING, S.FAILED, S.CANCELLED}),
        S.ANALYSING: frozenset({S.PUBLISHING, S.FAILED, S.CANCELLED}),
        S.PUBLISHING: frozenset({S.COMPLETED, S.FAILED, S.CANCELLED}),
        S.COMPLETED: frozenset(),
        S.FAILED: frozenset(),
        S.CANCELLED: frozenset(),
    }
)


def next_status(
    current: ReviewRunStatus, requested: ReviewRunStatus
) -> Result[ReviewRunStatus]:
    """Решить, может ли прогон перейти из `current` в `requested`."""
    if current in TERMINAL_STATUSES:
        return Result.failure(
            f"{current} is terminal; cannot move to {requested}"
        )
    if requested not in _ALLOWED[current]:
        return Result.failure(f"cannot move from {current} to {requested}")
    return Result.success(requested)


def advance(run: ReviewRun, requested: ReviewRunStatus, now: datetime) -> Result[ReviewRun]:
    """Вернуть прогон, переведённый в `requested`, или причину отказа.

    `now` передаётся, а не читается, поэтому тест проверяет точный timestamp.
    """
    verdict = next_status(run.status, requested)
    if not verdict.ok:
        return Result.failure(verdict.error or "rejected")
    return Result.success(
        replace(run, status=requested, last_progress_at=now, updated_at=now)
    )
