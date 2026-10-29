"""Выметание зависших прогонов.

На коммит допускается не больше одного нетерминального прогона, поэтому
упавший посреди работы воркер (или задача, не дошедшая до очереди) блокирует
коммит навсегда. Ключ в Redis с TTL истёк бы сам; ограничение в базе — нет,
поэтому уборка явная. Выбор прогонов — чистая `find_stale`; здесь только
перевод выбранных в `failed` и закрытие их брошенных шагов.
"""

from dataclasses import replace
from datetime import datetime, timedelta

from app.application.ports import UnitOfWork
from app.application.ports.run_outcome import NO_RUN_OUTCOME, RunOutcomeRecorder
from app.application.review_run_steps import close_abandoned_steps
from app.domain.enums import ReviewRunStatus
from app.domain.lifecycle import advance
from app.domain.staleness import find_stale, stale_limit_for


def sweep_stale_runs(
    uow: UnitOfWork,
    *,
    now: datetime,
    limit: timedelta,
    queued_limit: timedelta,
    run_outcome: RunOutcomeRecorder = NO_RUN_OUTCOME,
) -> int:
    """Перевести в `failed` прогоны без прогресса дольше их порога; вернуть их число.

    Порог — `limit`, у прогона в `queued` — `queued_limit` (`stale_limit_for`).

    Шаги, которые выметенный прогон оставил `running`, закрываются следом
    (`close_abandoned_steps`) — у каждого прогона своим коммитом. Переведённый
    прогон — терминальный исход, его пишет `run_outcome`: счётчик считает
    прогоны, а не обработки сообщений.
    """
    with uow:
        stale = find_stale(uow.review_runs.list_unfinished(), now, limit, queued_limit=queued_limit)
        for run in stale:
            run_limit = stale_limit_for(run, limit, queued_limit=queued_limit)
            uow.review_runs.update(
                replace(
                    advance(run, ReviewRunStatus.FAILED, now).unwrap(),
                    failure_reason=f"нет прогресса дольше {run_limit}",
                )
            )
        uow.commit()
    for run in stale:
        run_outcome.run_finished(ReviewRunStatus.FAILED.value)
        close_abandoned_steps(uow, run.id, now=now)
    return len(stale)
