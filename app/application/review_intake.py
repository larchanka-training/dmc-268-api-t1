"""Приём запроса на ревью: регистрация репозитория и запроса на изменение,
создание прогона и постановка задачи в очередь.

Use case уровня application, поэтому его можно вызвать не только из вебхука:
ручной запуск и `mention` (`TriggerSource.MANUAL`/`MENTION`) приходят сюда же
со своим `trigger`, без дубля upsert'ов. Разбор транспорта (заголовки, JSON
провайдера, подпись) остаётся в `app/api`, сюда попадает уже `ReviewRequest`.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from app.application.ports import UnitOfWork
from app.application.ports.job_queue import JobQueue
from app.domain.entities import MergeRequest, Repository, ReviewJob, ReviewRun
from app.domain.enums import (
    TERMINAL_STATUSES,
    MergeRequestState,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.lifecycle import advance

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    """Что нужно знать о запросе на ревью, независимо от того, откуда он пришёл."""

    provider: Provider
    trigger: TriggerSource
    event_type: str
    action: str
    repository_provider_id: str
    repository_full_name: str
    default_branch: str
    number: int
    title: str
    description: str
    author: str
    source_branch: str
    target_branch: str
    head_sha: str
    base_sha: str
    state: MergeRequestState


class IntakeOutcome(StrEnum):
    """Принят ли запрос новым прогоном или уже покрыт активным."""

    ENQUEUED = "enqueued"
    DUPLICATE = "duplicate"


def _upsert_repository(
    uow: UnitOfWork, request: ReviewRequest, now: datetime, new_id: Callable[[], UUID]
) -> Repository:
    repository = uow.repositories.find_by_provider(
        request.provider, request.repository_provider_id
    )
    if repository is not None:
        return repository
    repository = Repository(
        id=new_id(),
        provider=request.provider,
        provider_id=request.repository_provider_id,
        full_name=request.repository_full_name,
        default_branch=request.default_branch,
        # Регистрация с явной проверкой прав — отдельный, не начатый шов
        # (BACKEND_ARCHITECTURE.md, threat model). До него репозиторий,
        # впервые увиденный по вебхуку, ревьюится по умолчанию.
        auto_review_enabled=True,
        created_at=now,
        updated_at=now,
    )
    uow.repositories.add(repository)
    return repository


def _upsert_merge_request(
    uow: UnitOfWork,
    repository: Repository,
    request: ReviewRequest,
    now: datetime,
    new_id: Callable[[], UUID],
) -> MergeRequest:
    existing = uow.merge_requests.find_by_number(repository.id, request.number)
    if existing is None:
        merge_request = MergeRequest(
            id=new_id(),
            repository_id=repository.id,
            number=request.number,
            title=request.title,
            description=request.description,
            author=request.author,
            source_branch=request.source_branch,
            target_branch=request.target_branch,
            head_sha=request.head_sha,
            state=request.state,
            created_at=now,
            updated_at=now,
        )
        uow.merge_requests.add(merge_request)
        return merge_request
    updated = replace(
        existing,
        # Пустое значение — «источник не прислал», а не «очистить».
        title=request.title or existing.title,
        description=request.description or existing.description,
        source_branch=request.source_branch or existing.source_branch,
        target_branch=request.target_branch or existing.target_branch,
        head_sha=request.head_sha,
        state=request.state,
        updated_at=now,
    )
    uow.merge_requests.update(updated)
    return updated


def accept_review_request(
    request: ReviewRequest,
    *,
    uow: UnitOfWork,
    job_queue: JobQueue,
    now: datetime,
    new_id: Callable[[], UUID],
) -> IntakeOutcome:
    repository = _upsert_repository(uow, request, now, new_id)
    merge_request = _upsert_merge_request(uow, repository, request, now, new_id)

    if uow.review_runs.find_active(merge_request.id, request.head_sha) is not None:
        uow.commit()
        return IntakeOutcome.DUPLICATE

    run_id = new_id()
    uow.review_runs.add(
        ReviewRun(
            id=run_id,
            merge_request_id=merge_request.id,
            head_sha=request.head_sha,
            base_sha=request.base_sha,
            status=ReviewRunStatus.QUEUED,
            trigger=request.trigger,
            last_progress_at=now,
            created_at=now,
            updated_at=now,
        )
    )

    # Сначала commit, потом enqueue. Обратный порядок отдаёт сообщение воркеру
    # раньше, чем строка прогона видна в базе: воркер не находит прогон,
    # подтверждает сообщение, а API затем коммитит `queued` — задача потеряна,
    # и `find_active` блокирует коммит на каждую следующую доставку.
    uow.commit()

    try:
        job_queue.enqueue(
            ReviewJob(
                id=new_id(),
                review_run_id=run_id,
                event_type=request.event_type,
                action=request.action,
                repository_provider_id=repository.provider_id,
                repository_full_name=repository.full_name,
                pull_request_number=request.number,
                head_sha=request.head_sha,
                base_sha=request.base_sha,
            )
        )
    except Exception as exc:
        # «Закоммитили, но не поставили»: без компенсации прогон навсегда
        # остался бы `queued`, а повторная доставка получила бы «дубль» от
        # `find_active` и ничего не поставила. Ошибка идёт дальше — вебхук
        # получит 5xx, и GitHub повторит доставку. Если не удалась и
        # компенсация (база недоступна), прогон выметет `sweep_stale_runs`.
        _fail_unenqueued(uow, run_id, exc, now)
        raise
    return IntakeOutcome.ENQUEUED


def _fail_unenqueued(uow: UnitOfWork, run_id: UUID, cause: Exception, now: datetime) -> None:
    try:
        run = uow.review_runs.get(run_id)
        if run is None or run.status in TERMINAL_STATUSES:
            return
        failed = replace(
            advance(run, ReviewRunStatus.FAILED, now).unwrap(),
            failure_reason=f"не удалось поставить задачу в очередь: {cause}",
        )
        uow.review_runs.update(failed)
        uow.commit()
    except Exception:
        logger.exception("не удалось перевести прогон %s в failed после сбоя очереди", run_id)
