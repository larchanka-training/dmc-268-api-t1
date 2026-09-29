"""Use case приёма вебхука GitHub: от события до задачи в очереди.

Оркестрирует чистые функции домена и порты: извлекает событие из payload'а,
ищет зарегистрированный репозиторий, достаёт дифф и метаданные через
`VcsGateway` (сбой — 502 ещё до создания записей), создаёт `ReviewRun` и
ставит задачу в очередь. Сам дифф use case не разбирает: разбор и фильтрация
— территория воркера (design D3), который повторно достанет дифф по
идентификаторам из задачи. Зависимости — только порты (`UnitOfWork`,
`VcsGateway`, `JobQueue`), поэтому юнит-тесты обходятся фейками без базы,
брокера и сети.

Время и идентификаторы приходят аргументами (`now`, `new_id`), а не читаются
из часов или генератора: тест проверяет точный timestamp и идентификатор.
Повторная доставка на тот же коммит не создаёт второй прогон: `find_active`
находит существующий активный прогон, и use case возвращает «ignored» без
записи и без задачи — частичный уникальный индекс схемы страхует гонку.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.application.ports import JobQueue, UnitOfWork, VcsGateway
from app.application.ports.vcs_gateway import VcsError
from app.domain.entities import (
    MergeRequest,
    PRMetadata,
    ReviewJob,
    ReviewRun,
    WebhookEvent,
)
from app.domain.enums import (
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.webhook import extract_github_event


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    """Результат приёма вебхука: одна из трёх веток.

    `ignored` — событие не запускает ревью (`reopened`, неизвестное действие,
    незарегистрированный репозиторий, повторная доставка на тот же коммит):
    записей нет, задачи нет, ответ — 202. `failure` — VCS недоступен: прогон
    не создан, задачи нет, ответ — 502. `success` — прогон создан и задача
    поставлена, ответ — 202.
    """

    kind: Literal["ignored", "failure", "success"]
    review_run_id: UUID | None = None


def handle_webhook_event(
    payload: dict[str, object],
    uow: UnitOfWork,
    vcs: VcsGateway,
    queue: JobQueue,
    *,
    now: Callable[[], datetime],
    new_id: Callable[[], UUID],
) -> WebhookOutcome:
    """Принять вебхук: от payload'а до задачи в очереди.

    `now` и `new_id` передаются вызывающим, чтобы тесты фиксировали точные
    значения. Вне тестов это `datetime.now(UTC)` и `app.domain.ids.new_id`.
    """
    event = extract_github_event(payload)
    if event is None:
        return WebhookOutcome(kind="ignored")

    with uow as work:
        repository = work.repositories.find_by_provider(
            Provider.GITHUB, event.repo_provider_id
        )
        if repository is None:
            return WebhookOutcome(kind="ignored")

        try:
            # Дифф достаётся здесь, чтобы сбой VCS дал 502 до создания записей;
            # разбирает его воркер (D3), поэтому результат не нужен.
            vcs.fetch_diff(
                event.repo_full_name, event.pr_number, event.installation_id
            )
            metadata = vcs.fetch_pr_metadata(
                event.repo_full_name, event.pr_number, event.installation_id
            )
        except VcsError:
            return WebhookOutcome(kind="failure")

        merge_request = _upsert_merge_request(
            work, repository.id, event, metadata, now(), new_id
        )
        existing = work.review_runs.find_active(merge_request.id, event.head_sha)
        if existing is not None:
            return WebhookOutcome(kind="ignored", review_run_id=existing.id)

        run = _create_review_run(work, merge_request.id, event, metadata, now(), new_id())
        work.commit()

    queue.enqueue(
        ReviewJob(
            job_id=new_id(),
            review_run_id=run.id,
            repository_full_name=event.repo_full_name,
            repository_provider_id=int(event.repo_provider_id),
            pr_number=event.pr_number,
            head_sha=event.head_sha,
            base_sha=metadata.base_sha,
            action=event.action,
            priority=0,
        )
    )
    return WebhookOutcome(kind="success", review_run_id=run.id)


def _upsert_merge_request(
    work: UnitOfWork,
    repository_id: UUID,
    event: WebhookEvent,
    metadata: PRMetadata,
    now: datetime,
    new_id: Callable[[], UUID],
) -> MergeRequest:
    """Найти существующий PR по номеру или создать новый; сдвинуть head_sha."""
    existing = work.merge_requests.find_by_number(repository_id, event.pr_number)
    if existing is None:
        created = MergeRequest(
            id=new_id(),
            repository_id=repository_id,
            number=event.pr_number,
            title=event.title,
            description="",
            author=event.author,
            source_branch=event.source_branch,
            target_branch=event.target_branch,
            head_sha=event.head_sha,
            state=metadata.state,
            created_at=now,
            updated_at=now,
        )
        work.merge_requests.add(created)
        return created
    updated = replace(
        existing,
        title=event.title,
        source_branch=event.source_branch,
        target_branch=event.target_branch,
        head_sha=event.head_sha,
        state=metadata.state,
        updated_at=now,
    )
    work.merge_requests.update(updated)
    return updated


def _create_review_run(
    work: UnitOfWork,
    merge_request_id: UUID,
    event: WebhookEvent,
    metadata: PRMetadata,
    now: datetime,
    run_id: UUID,
) -> ReviewRun:
    """Создать прогон в состоянии queued с источником webhook."""
    run = ReviewRun(
        id=run_id,
        merge_request_id=merge_request_id,
        head_sha=event.head_sha,
        base_sha=metadata.base_sha,
        status=ReviewRunStatus.QUEUED,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=now,
        created_at=now,
        updated_at=now,
    )
    work.review_runs.add(run)
    return run
