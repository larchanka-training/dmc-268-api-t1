"""Use case приёма вебхука GitHub: от события до задачи в очереди.

Оркестрирует чистые функции домена и порты: извлекает событие из payload'а,
ищет зарегистрированный репозиторий, достаёт дифф и метаданные через
`VcsGateway` (сбой — 502 ещё до создания записей) и создаёт `ReviewRun`.
Сам дифф use case не разбирает и не хранит: разбор — территория воркера
(design D3), который повторно достанет дифф по идентификаторам из задачи.
Зависимости — только порты (`UnitOfWork`, `VcsGateway`, `JobQueue`),
поэтому юнит-тесты обходятся фейками без базы, брокера и сети.

Транзакции короткие: репозиторий ищется отдельным чтением, VCS-вызовы идут
между транзакциями — соединение пула не висит idle in transaction, пока
отвечает GitHub. Запись — вторая транзакция, одна на merge_request и прогон.

Порядок «коммит, потом задача» — по воркеру #36: тот рассчитывает, что прогон
закоммичен до постановки задачи, и сообщение без строки кладёт в DLQ.
Обратная цена: при сбое брокера прогон остаётся закоммиченным `queued` без
сообщения — его переведёт в `failed` sweep обработки (`list_unfinished`);
повторная доставка до того находит активный прогон и отвечает `duplicate`,
не плодя второй.

Повторная доставка на тот же коммит не создаёт второй прогон: `find_active`
находит существующий активный (исход `duplicate`), а гонку параллельных
доставок страхуют частичные уникальные индексы — отказ любого из них
адаптер хранилища переводит в `ActiveRunConflict`, и проигравший по
естественному ключу (репозиторий, номер) находит записи победителя и
возвращает `conflict`, не падая и не отвечая 5xx. Дубль проверяется до
upsert'а merge_request: на PR один активный прогон, повторная доставка
ничего не переписывает.

Время и идентификаторы приходят аргументами (`now`, `new_id`), а не читаются
из часов или генератора: тест проверяет точный timestamp и идентификатор.
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import UUID

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


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    """Результат приёма вебхука: одна из пяти веток.

    `ignored` — событие не запускает ревью (`reopened`, неизвестное действие,
    незарегистрированный репозиторий): записей нет, задачи нет. `failure` —
    VCS недоступен: прогон не создан, задачи нет. `duplicate` — на этот
    коммит уже есть активный прогон, найденный до вставки и до обновления
    merge_request. `conflict` — гонку отклонил уникальный индекс, проигравший
    откатился и нашёл записи победителя. Оба исхода находят существующий
    прогон: не-2xx хостинг считает неудачной доставкой и повторяет её, поэтому
    ответ — 202 с проекцией найденного прогона (контракт `openapi.yaml`).
    В `duplicate`, `conflict` и `success` заполнены `run`, `merge_request`
    и `repository` — из них HTTP-слой собирает проекцию `ReviewJob`; use case
    сам форму контракта не знает.
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
    *,
    now: Callable[[], datetime],
    new_id: Callable[[], UUID],
) -> WebhookOutcome:
    """Принять вебхук: от payload'а до закоммиченного прогона и задачи.

    `now` и `new_id` передаются вызывающим, чтобы тесты фиксировали точные
    значения. Вне тестов это `datetime.now(UTC)` и `app.domain.ids.new_id`.
    """
    event = extract_github_event(payload)
    if event is None:
        return WebhookOutcome(kind="ignored")

    # Короткое чтение: транзакция живёт только на SELECT репозитория.
    with uow as work:
        repository = work.repositories.find_by_provider(
            Provider.GITHUB, event.repo_provider_id
        )
    if repository is None:
        return WebhookOutcome(kind="ignored")

    # VCS между транзакциями: соединение пула не занято, пока GitHub отвечает.
    try:
        # Дифф достаётся здесь, чтобы сбой VCS дал 502 до создания записей;
        # разбирает его воркер (D3), поэтому результат не нужен.
        vcs.fetch_diff(event.repo_full_name, event.pr_number, event.installation_id)
        metadata = vcs.fetch_pr_metadata(
            event.repo_full_name, event.pr_number, event.installation_id
        )
    except VcsError:
        return WebhookOutcome(kind="failure")

    with uow as work:
        # Дубль проверяется до upsert'а merge_request: на PR один активный
        # прогон, повторная доставка его не трогает.
        existing_merge_request = work.merge_requests.find_by_number(
            repository.id, event.pr_number
        )
        if existing_merge_request is not None:
            existing_run = work.review_runs.find_active(
                existing_merge_request.id, event.head_sha
            )
            if existing_run is not None:
                return WebhookOutcome(
                    kind="duplicate",
                    run=existing_run,
                    merge_request=existing_merge_request,
                    repository=repository,
                )

        merge_request = _upsert_merge_request(
            work, repository.id, existing_merge_request, event, metadata, now(), new_id
        )
        run = _create_review_run(work, merge_request.id, event, metadata, now(), new_id())
        try:
            work.commit()
        except ActiveRunConflict:
            # Индекс сказал, что победитель закоммитил раньше: либо активный
            # прогон на этот коммит, либо merge_request этого PR. Наша вставка
            # откатилась, поэтому записи победителя ищутся по естественному
            # ключу (репозиторий, номер), а не по идентификатору нашей вставки.
            work.rollback()
            survivor_run, survivor_mr = _find_survivor(
                work, repository.id, event.pr_number, event.head_sha
            )
            if survivor_run is None or survivor_mr is None:
                raise
            return WebhookOutcome(
                kind="conflict",
                run=survivor_run,
                merge_request=survivor_mr,
                repository=repository,
            )

    # Задача — после коммита: воркер #36 ожидает, что прогон уже виден в базе,
    # и сообщение без строки кладёт в DLQ. Сбой здесь оставляет закоммиченный
    # queued-прогон без сообщения — его подберёт sweep обработки (#36).
    queue.enqueue(
        ReviewJob(
            id=run.id,
            event_type="pull_request",
            action=event.action,
            repository_provider_id=event.repo_provider_id,
            repository_full_name=event.repo_full_name,
            pull_request_number=event.pr_number,
            head_sha=event.head_sha,
            base_sha=metadata.base_sha,
        )
    )
    return WebhookOutcome(
        kind="success", run=run, merge_request=merge_request, repository=repository
    )


def _find_survivor(
    work: UnitOfWork, repository_id: UUID, pr_number: int, head_sha: str
) -> tuple[ReviewRun | None, MergeRequest | None]:
    """Записи победителя гонки после отката: merge_request по естественному
    ключу, активный прогон — по нему и доставленному коммиту."""
    survivor_mr = work.merge_requests.find_by_number(repository_id, pr_number)
    if survivor_mr is None:
        return None, None
    return work.review_runs.find_active(survivor_mr.id, head_sha), survivor_mr


def _upsert_merge_request(
    work: UnitOfWork,
    repository_id: UUID,
    existing: MergeRequest | None,
    event: WebhookEvent,
    metadata: PRMetadata,
    now: datetime,
    new_id: Callable[[], UUID],
) -> MergeRequest:
    """Обновить найденный PR свежими метаданными VCS или создать новый.

    Заголовок, автор и ветки берутся из свежих метаданных VCS, а не из
    payload'а: они приезжают тем же запросом, что и `base_sha`, и актуальнее
    того, что успела доставить очередь вебхуков (заголовок могли отредактировать
    между доставкой и обработкой). `head_sha` — из события: прогон создаётся
    для доставленного коммита. Вызывается только когда дубля нет: обновление
    без будущего коммита откатилось бы вместе с транзакцией.
    """
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
