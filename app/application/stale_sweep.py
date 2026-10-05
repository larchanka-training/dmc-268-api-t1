"""Выметание зависших прогонов.

На коммит допускается не больше одного нетерминального прогона, поэтому
упавший посреди работы воркер (или задача, не дошедшая до очереди) блокирует
коммит навсегда. Ключ в Redis с TTL истёк бы сам; ограничение в базе — нет,
поэтому уборка явная. Выбор прогонов — чистая `find_stale`; здесь только
перевод выбранных в `failed`.
"""

from dataclasses import replace
from datetime import datetime, timedelta

from app.application.ports import UnitOfWork
from app.domain.enums import ReviewRunStatus
from app.domain.lifecycle import advance
from app.domain.staleness import find_stale


def sweep_stale_runs(uow: UnitOfWork, *, now: datetime, limit: timedelta) -> int:
    """Перевести в `failed` прогоны без прогресса дольше `limit`; вернуть их число."""
    with uow:
        stale = find_stale(uow.review_runs.list_unfinished(), now, limit)
        for run in stale:
            uow.review_runs.update(
                replace(
                    advance(run, ReviewRunStatus.FAILED, now).unwrap(),
                    failure_reason=f"нет прогресса дольше {limit}",
                )
            )
        uow.commit()
    return len(stale)
