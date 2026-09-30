"""Use case приёма вебхука GitHub: от события до задачи в очереди.

Оркестрирует чистые функции домена и порты: извлекает событие из payload'а,
ищет зарегистрированный репозиторий, достаёт дифф и метаданные через
`VcsGateway` (сбой — 502 ещё до создания записей), кладёт дифф в `CacheStore`
и создаёт `ReviewRun`. Сам дифф use case не разбирает: разбор и фильтрация —
территория воркера (design D3), который возьмёт его из кэша или повторно
достанет по идентификаторам из задачи. Зависимости — только порты
(`UnitOfWork`, `VcsGateway`, `JobQueue`, `CacheStore`), поэтому юнит-тесты
обходятся фейками без базы, брокера и сети.

Время и идентификаторы приходят аргументами (`now`, `new_id`), а не читаются
из часов или генератора: тест проверяет точный timestamp и идентификатор.

Порядок «задача в брокере, потом коммит» выбран сознательно: если брокер
недоступен, транзакция откатывается вместе с прогоном — в базе не остаётся
навсегда `queued` записи без сообщения, которая закрыла бы коммит от новых
прогонов. Обратная цена: при гонке двух доставок сообщение проигравшего
переживает откат; воркер отбрасывает задачу о прогоне, которого нет.

Повторная доставка на тот же коммит не создаёт второй прогон: `find_active`
находит существующий активный (исход `duplicate`), а гонку параллельных
доставок страхует частичный уникальный индекс схемы — его отказ адаптер
хранилища переводит в `ActiveRunConflict`, и проигравший возвращает
`conflict`, не падая и не отвечая 5xx.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.application.ports.cache_store import CacheStore
from app.application.ports.job_queue import JobQueue
from app.application.ports.unit_of_work import ActiveRunConflict, UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.domain.entities import (
    MergeRequest,
    PRMetadata,
    Repository,
    ReviewJob,
    ReviewRun,
    WebhookEvent,
)
from app.domain.enums import (
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.vcs_errors import VcsError
from app.domain.webhook import extract_github_event

# Дифф в кэше живёт до конца обработки задачи: воркер подхватывает её
# за секунды, час — запас на очередь из простоявших прогонов.
_DIFF_TTL_SECONDS = 60 * 60


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    """Результат приёма вебхука: одна из пяти веток.

    `ignored` — событие не запускает ревью (`reopened`, неизвестное действие,
    незарегистрированный репозиторий): записей нет, задачи нет. `failure` —
    VCS недоступен: прогон не создан, задачи нет. `duplicate` — на этот коммит
    уже есть активный прогон, найденный до вставки: ответ — 202 с проекцией
    существующего прогона. `conflict` — гонка, вставку отклонил уникальный
    индекс: ответ — 409. В `duplicate` и `success` заполнены `run`,
    `merge_request` и `repository` — из них HTTP-слой собирает проекцию
    `ReviewJob`; use case сам форму контракта не знает.
    """

    kind: Literal["ignored", "failure", "duplicate", "conflict", "success"]
    run: ReviewRun | None = None
    merge_request: MergeRequest | None = None
    repository: Repository | None = None


def handle_webhook_event(
    payload: dict[str, object],
    uow: UnitOfWork,
    vcs: VcsGateway,
    queue: JobQueue,
    cache: CacheStore,
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

        diff_key = _diff_key(event)
        diff = cache.get(diff_key)
        if diff is None:
            try:
                # Дифф достаётся здесь, чтобы сбой VCS дал 502 до создания
                # записей; разбирает его воркер (D3), поэтому в записях он
                # не живёт — вместо этого кладётся в кэш для воркера (§4.3).
                diff = vcs.fetch_diff(
                    event.repo_full_name, event.pr_number, event.installation_id
                )
            except VcsError:
                return WebhookOutcome(kind="failure")
            cache.put(diff_key, diff, ttl_seconds=_DIFF_TTL_SECONDS)

        try:
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
            return WebhookOutcome(
                kind="duplicate",
                run=existing,
                merge_request=merge_request,
                repository=repository,
            )

        run = _create_review_run(work, merge_request.id, event, metadata, now(), new_id())
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
            )
        )
        try:
            work.commit()
        except ActiveRunConflict:
            # Индекс сказал, что победитель уже закоммитил активный прогон
            # на этот коммит. Откат убирает нашу вставку; в новой транзакции
            # прогон победителя уже виден — отвечаем конфликтом, а не 5xx.
            work.rollback()
            survivor = work.review_runs.find_active(merge_request.id, event.head_sha)
            if survivor is None:
                raise
            return WebhookOutcome(
                kind="conflict",
                run=survivor,
                merge_request=merge_request,
                repository=repository,
            )

    return WebhookOutcome(
        kind="success", run=run, merge_request=merge_request, repository=repository
    )


def _diff_key(event: WebhookEvent) -> str:
    """Ключ диффа в кэше: содержимое однозначно определяется тройкой."""
    return f"diff:{event.repo_full_name}:{event.pr_number}:{event.head_sha}"


def _upsert_merge_request(
    work: UnitOfWork,
    repository_id: UUID,
    event: WebhookEvent,
    metadata: PRMetadata,
    now: datetime,
    new_id: Callable[[], UUID],
) -> MergeRequest:
    """Найти существующий PR по номеру или создать новый.

    Заголовок, автор и ветки берутся из свежих метаданных VCS, а не из
    payload'а: они приезжают тем же запросом, что и `base_sha`, и актуальнее
    того, что успела доставить очередь вебхуков (заголовок могли отредактировать
    между доставкой и обработкой). `head_sha` — из события: прогон создаётся
    для доставленного коммита.
    """
    existing = work.merge_requests.find_by_number(repository_id, event.pr_number)
    if existing is None:
        created = MergeRequest(
            id=new_id(),
            repository_id=repository_id,
            number=event.pr_number,
            title=metadata.title,
            description="",
            author=metadata.author,
            source_branch=metadata.source_branch,
            target_branch=metadata.target_branch,
            head_sha=event.head_sha,
            state=metadata.state,
            created_at=now,
            updated_at=now,
        )
        work.merge_requests.add(created)
        return created
    updated = replace(
        existing,
        title=metadata.title,
        source_branch=metadata.source_branch,
        target_branch=metadata.target_branch,
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
