"""Выметание зависших прогонов.

На коммит допускается не больше одного нетерминального прогона, поэтому
упавший посреди работы воркер (или задача, не дошедшая до очереди) блокирует
коммит навсегда. Ключ в Redis с TTL истёк бы сам; ограничение в базе — нет,
поэтому уборка явная. Выбор прогонов — чистая `find_stale`; здесь только
перевод выбранных в `failed`, закрытие их брошенных шагов и merge gate head.
"""

import logging
from dataclasses import replace
from datetime import datetime, timedelta

from app.application.ports import InstallationCatalog, UnitOfWork
from app.application.ports.run_outcome import NO_RUN_OUTCOME, RunOutcomeRecorder
from app.application.ports.unit_of_work import RunStateConflict
from app.application.ports.vcs_gateway import VcsGateway
from app.application.review_pipeline import block_head_of_failed_run
from app.application.review_run_steps import close_abandoned_steps
from app.domain.entities import ReviewRun
from app.domain.enums import ReviewRunStatus
from app.domain.lifecycle import advance
from app.domain.staleness import find_stale, stale_limit_for
from app.domain.vcs_errors import VcsTransientError

logger = logging.getLogger(__name__)


def sweep_stale_runs(
    uow: UnitOfWork,
    *,
    now: datetime,
    limit: timedelta,
    queued_limit: timedelta,
    run_outcome: RunOutcomeRecorder = NO_RUN_OUTCOME,
    vcs: VcsGateway | None = None,
    installations: InstallationCatalog | None = None,
) -> int:
    """Перевести в `failed` прогоны без прогресса дольше их порога; вернуть их число.

    Порог — `limit`, у прогона в `queued` — `queued_limit` (`stale_limit_for`).

    Запись условна и у каждого прогона в своей транзакции: прогон, который с момента
    чтения продвинул воркер или закрыла другая очистка, пропускается, а остальные
    выметаются. Шаги, которые выметенный прогон оставил `running`, закрываются следом
    (`close_abandoned_steps`) своим коммитом. Переведённый прогон — терминальный исход,
    его пишет `run_outcome`: счётчик считает прогоны, а не обработки сообщений.

    С `vcs` и `installations` выметенный прогон выставляет gate head по правилам
    упавшего (`block_head_of_failed_run`); без них check run не трогается. Проход идёт
    в consume-цикле воркера: после первого временного сбоя GitHub (сеть, лимит) gate
    до конца прохода не ставится, иначе каждый следующий прогон ждал бы таймауты.
    """
    with uow:
        stale = find_stale(uow.review_runs.list_unfinished(), now, limit, queued_limit=queued_limit)
    swept = 0
    github_up = True
    for run in stale:
        run_limit = stale_limit_for(run, limit, queued_limit=queued_limit)
        failed = replace(
            advance(run, ReviewRunStatus.FAILED, now).unwrap(),
            failure_reason=f"нет прогресса дольше {run_limit}",
        )
        try:
            with uow:
                uow.review_runs.update(failed, previous=run)
                uow.commit()
        except RunStateConflict:
            logger.info(
                "прогон изменился после чтения, очистка его пропускает",
                extra={"review_run_id": str(run.id)},
            )
            continue
        swept += 1
        run_outcome.run_finished(ReviewRunStatus.FAILED.value)
        close_abandoned_steps(uow, run.id, now=now)
        if github_up and vcs is not None and installations is not None:
            github_up = _block_swept_head(uow, vcs, installations, run)
    return swept


def _block_swept_head(
    uow: UnitOfWork, vcs: VcsGateway, installations: InstallationCatalog, run: ReviewRun
) -> bool:
    """Gate head выметенного прогона, как у упавшего в воркере.

    Номер установки был только в сообщении очереди, поэтому он спрашивается у GitHub по
    репозиторию прогона. Best-effort: сбой GitHub или установка, которой нет, пишется в
    лог и не прерывает проход; сбой самого check run глотает `block_head_of_failed_run`.
    `False` — GitHub временно недоступен, дальше в проходе к нему не ходить.
    """
    extra = {"review_run_id": str(run.id)}
    try:
        with uow:
            merge_request = uow.merge_requests.get(run.merge_request_id)
            repository = uow.repositories.get(merge_request.repository_id) if merge_request else None
        if repository is None:
            return True
        installation_id = installations.repository_installation_id(repository.full_name)
    except VcsTransientError:
        logger.exception(
            "выметенный прогон: GitHub недоступен, gate до конца прохода не выставляется",
            extra=extra,
        )
        return False
    except Exception:
        logger.exception("выметенный прогон: номер установки не получен, gate не выставлен", extra=extra)
        return True
    if installation_id is None:
        logger.warning(
            "выметенный прогон: приложение не установлено на %s или установка приостановлена, "
            "gate не выставлен",
            repository.full_name,
            extra=extra,
        )
        return True
    block_head_of_failed_run(
        uow,
        vcs,
        run_id=run.id,
        head_sha=run.head_sha,
        repository_full_name=repository.full_name,
        installation_id=installation_id,
    )
    return True
