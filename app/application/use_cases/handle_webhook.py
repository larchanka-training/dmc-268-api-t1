"""Use case приёма вебхука GitHub: от события до задачи в очереди.

Оркестрирует чистые функции домена и порты: извлекает событие из payload'а,
ищет зарегистрированный репозиторий, достаёт метаданные PR через
`VcsGateway` (сбой — 502 ещё до создания записей) и создаёт `ReviewRun`.
Дифф use case не запрашивает: его размер задаёт автор PR, а GitHub ждёт ответа
на доставку 10 секунд. Дифф с лимитом размера достаёт и разбирает воркер
(design D3) по идентификаторам из задачи (#144).
Зависимости — только порты (`UnitOfWork`, `VcsGateway`, `JobQueue`),
поэтому юнит-тесты обходятся фейками без базы, брокера и сети.

Транзакции короткие: репозиторий ищется отдельным чтением, VCS-вызовы идут
между транзакциями — соединение пула не висит idle in transaction, пока
отвечает GitHub. Запись — вторая транзакция, одна на merge_request и прогон.

Порядок «коммит, потом задача» — по воркеру #36: тот рассчитывает, что прогон
закоммичен до постановки задачи, и сообщение без строки кладёт в DLQ. Сбой
постановки после коммита компенсируется сразу (`_fail_unenqueued`): прогон
переводится в `failed` — `failed` не блокирует повторную доставку ни через
`find_active`, ни через `find_reviewed`, и следующая доставка создаёт новый прогон.
Оставленный `queued` без задачи сделал бы коммит навсегда без ревью: повторная
доставка до sweep получала бы `duplicate` и задачу больше не ставила. Если не
удалась и компенсация, прогон выметет sweep обработки (`list_unfinished`, #36).

Повторная доставка на тот же коммит не создаёт второй прогон: `find_active`
находит существующий активный, `find_reviewed` — завершённый не-failed прогон
по вебхуку (исход `duplicate` в обоих случаях, #75), если последний прогон на
коммит не упал и не отменён. Смена целевой ветки (`edited` с другой веткой, чем
записана у PR, #222) даёт новый прогон: активный на старой ветке отменяется, а
head блокируется после коммита, до постановки задачи. Таблицы ключей
`X-GitHub-Delivery` нет: повтор после нашего сбоя постановки приходит с тем же
ключом и был бы отброшен, а так `failed` прогон повтор не блокирует. Гонку параллельных
доставок страхуют частичные уникальные индексы — отказ любого из них
адаптер хранилища переводит в `ActiveRunConflict`. Проигравший после отката
ищет записи победителя по естественному ключу (репозиторий, номер): у
победителя с тем же коммитом есть активный прогон — исход `conflict`, 202 с
его проекцией; у победителя с другим коммитом (opened и synchronize в окне
гонки) есть только merge_request — запись повторяется поверх его записей один
раз: update его merge_request и новый прогон на доставленный коммит. Второй
отказ индекса — 5xx: GitHub запишет доставку неудавшейся, но сам её не
повторит — её переотправит проход воркера (`redeliver_failed_webhooks`, #149).
Дубль проверяется до upsert'а merge_request: на PR один активный прогон,
повторная доставка ничего не переписывает.

Время и идентификаторы приходят аргументами (`now`, `new_id`), а не читаются
из часов или генератора: тест проверяет точный timestamp и идентификатор.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.application.merge_gate_publish import build_retarget_check
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
    TERMINAL_STATUSES,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.lifecycle import advance
from app.domain.vcs_errors import VcsError
from app.domain.webhook import extract_github_event

logger = logging.getLogger(__name__)

# Последний прогон на коммит в этих статусах повтор доставки не блокирует.
_RETRYABLE_STATUSES = frozenset({ReviewRunStatus.FAILED, ReviewRunStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    """Результат приёма вебхука: одна из пяти веток.

    `ignored` — событие не запускает ревью (`reopened`, неизвестное действие,
    незарегистрированный репозиторий): записей нет, задачи нет. `failure` —
    VCS недоступен: прогон не создан, задачи нет. `duplicate` — на этот
    коммит уже есть активный или завершённый не-failed прогон по вебхуку,
    найденный до вставки и до обновления merge_request. `conflict` — гонку отклонил уникальный индекс, проигравший
    откатился и нашёл записи победителя. Оба исхода находят существующий
    прогон: дубль — не ошибка, не-2xx GitHub записал бы неудавшейся доставкой,
    поэтому ответ — 202 с проекцией найденного прогона (`ReviewJob` в схеме API).
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
    # Только метаданные: base_sha берётся из них (спека webhook-intake) и
    # уходит в сообщение вместе с доставленным head_sha — по этой паре воркер
    # достанет дифф, так что после `synchronize` ревьюится доставленный коммит.
    try:
        metadata = vcs.fetch_pr_metadata(
            event.repo_full_name, event.pr_number, event.installation_id
        )
    except VcsError:
        return WebhookOutcome(kind="failure")

    retarget = False
    with uow as work:
        # Дубль проверяется до upsert'а merge_request: на PR один активный
        # прогон, повторная доставка его не трогает.
        existing_merge_request = work.merge_requests.find_by_number(
            repository.id, event.pr_number
        )
        if existing_merge_request is not None:
            # Смена целевой ветки (#222) — только `edited` с другой веткой, чем
            # записана у PR. Вершина ветки (`base_sha`) двигается и без смены
            # ветки — от неё повтор `synchronize` дублем быть не перестаёт.
            # Событие, чья ветка уже не та, что в свежих метаданных, устарело:
            # ветку с тех пор сменили снова, и ретаргетом займётся свежее событие.
            retarget = (
                event.action == "edited"
                and event.target_branch == metadata.target_branch
                and existing_merge_request.target_branch != metadata.target_branch
            )
            duplicate_of = None if retarget else _duplicate_of(
                work, existing_merge_request.id, event.head_sha
            )
            if duplicate_of is not None:
                return WebhookOutcome(
                    kind="duplicate",
                    run=duplicate_of,
                    merge_request=existing_merge_request,
                    repository=repository,
                )

    with uow as work:
        if retarget and existing_merge_request is not None:
            # Прогон на старой ветке ещё идёт — выставил бы gate по диффу, которого
            # в PR уже нет. Отмена — своим коммитом до вставки нового прогона:
            # частичный индекс допускает один активный прогон на коммит.
            _supersede(work, existing_merge_request.id, event.head_sha, now())

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
            if survivor_run is not None:
                return WebhookOutcome(
                    kind="conflict",
                    run=survivor_run,
                    merge_request=survivor_mr,
                    repository=repository,
                )
            if survivor_mr is None:
                # Ни merge_request, ни активного прогона на наш коммит нет —
                # это не гонка доставок, отказ индекса означает настоящую
                # ошибку данных.
                raise
            # Победитель нёс другой коммит (opened и synchronize в окне
            # гонки): его merge_request уже есть, а активного прогона на наш
            # head_sha нет. Запись повторяется поверх его записей: update
            # его merge_request и новый прогон на доставленный коммит.
            merge_request = _upsert_merge_request(
                work, repository.id, survivor_mr, event, metadata, now(), new_id
            )
            run = _create_review_run(work, merge_request.id, event, metadata, now(), new_id())
            try:
                work.commit()
            except ActiveRunConflict:
                # Между откатом и повтором активный прогон на наш коммит
                # создала третья доставка — возвращаем её прогон. Любой
                # другой повторный отказ — наверх: 5xx. Сам GitHub упавшую
                # доставку не повторяет: её переотправляет проход воркера (#149).
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

    if retarget:
        # Check run висит на коммите: до ревью нового диффа на нём остался бы
        # `success` старого. Блокировка — после коммита отмены и нового прогона:
        # воркер старого прогона и пересчёт по треду, выставив gate, перечитывают
        # последний прогон на head (`settle_merge_gate`), так что любая
        # перестановка с ними кончается `failure`. Задача — после блокировки:
        # новый прогон не выставит gate раньше неё.
        try:
            vcs.set_merge_gate_check(
                event.repo_full_name,
                event.head_sha,
                event.installation_id,
                build_retarget_check(),
            )
        except VcsError as exc:
            # 502 и откат: прогон — в `failed` (повтор доставки он дублем не
            # делает), PR — на прежнюю ветку, чтобы повтор (#149) снова увидел
            # смену и заблокировал head.
            logger.warning(
                "merge gate %s@%s не заблокирован после смены целевой ветки",
                event.repo_full_name,
                event.head_sha,
                exc_info=True,
            )
            _fail_unenqueued(uow, run.id, exc, now())
            if existing_merge_request is not None:
                _restore_target_branch(
                    uow, merge_request.id, existing_merge_request.target_branch, now()
                )
            return WebhookOutcome(kind="failure")

    # Задача — после коммита: воркер #36 ожидает, что прогон уже виден в базе,
    # и сообщение без строки кладёт в DLQ. `id` — идентификатор сообщения,
    # новый на каждую постановку (§4.2, спека job-queue); прогон указывает
    # `review_run_id`, так что две постановки одного прогона различимы в DLQ.
    try:
        queue.enqueue(
            ReviewJob(
                id=new_id(),
                review_run_id=run.id,
                event_type="pull_request",
                action=event.action,
                installation_id=event.installation_id,
                repository_provider_id=event.repo_provider_id,
                repository_full_name=event.repo_full_name,
                pull_request_number=event.pr_number,
                head_sha=event.head_sha,
                base_sha=metadata.base_sha,
            )
        )
    except Exception as exc:
        # «Закоммитили, но не поставили»: оставшийся queued блокировал бы
        # коммит от ревью навсегда — повторная доставка до sweep получала бы
        # duplicate и задачу больше не ставила. Прогон переводится в failed:
        # терминальное состояние не блокирует следующую доставку. Ошибка идёт
        # дальше — вебхук ответит 5xx. Сам GitHub упавшую доставку не повторяет:
        # её переотправляет проход воркера (#149).
        _fail_unenqueued(uow, run.id, exc, now())
        raise
    return WebhookOutcome(
        kind="success", run=run, merge_request=merge_request, repository=repository
    )


def _duplicate_of(work: UnitOfWork, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
    """Прогон, повтором доставки которого считается вебхук на этот коммит.

    Активный прогон или завершённый не-failed по вебхуку (#75) — но только если
    последний прогон на коммит не `failed` и не `cancelled`: прогон после смены
    ветки упал или отменён — head не проверен, и повтор доставки даёт новый
    прогон, а не дубль более старого (#222)."""
    latest = work.review_runs.find_latest(merge_request_id, head_sha)
    if latest is None or latest.status in _RETRYABLE_STATUSES:
        return None
    return work.review_runs.find_active(merge_request_id, head_sha) or (
        work.review_runs.find_reviewed(merge_request_id, head_sha)
    )


def _supersede(work: UnitOfWork, merge_request_id: UUID, head_sha: str, now: datetime) -> None:
    """Отменить активный прогон на этот коммит, если он есть: он ревьюит дифф
    против прежней целевой ветки."""
    run = work.review_runs.find_active(merge_request_id, head_sha)
    if run is None:
        return
    cancelled = advance(run, ReviewRunStatus.CANCELLED, now)
    if not cancelled.ok:
        return
    work.review_runs.update(cancelled.unwrap())
    work.commit()
    logger.info(
        "прогон %s отменён: целевая ветка PR сменилась, база %s устарела", run.id, run.base_sha
    )


def _fail_unenqueued(
    uow: UnitOfWork, run_id: UUID, cause: Exception, now: datetime
) -> None:
    """Перевести закоммиченный прогон в failed, когда задача не ушла в брокер.

    Отказ компенсации (база недоступна) не маскирует исходную ошибку —
    прогон останется до sweep обработки (#36).
    """
    try:
        with uow as work:
            run = work.review_runs.get(run_id)
            if run is None or run.status in TERMINAL_STATUSES:
                return
            failed = replace(
                advance(run, ReviewRunStatus.FAILED, now).unwrap(),
                failure_reason=f"не удалось поставить задачу в очередь: {cause}",
            )
            work.review_runs.update(failed)
            work.commit()
    except Exception:
        logger.exception("не удалось перевести прогон %s в failed после сбоя очереди", run_id)


def _restore_target_branch(
    uow: UnitOfWork, merge_request_id: UUID, target_branch: str, now: datetime
) -> None:
    """Вернуть PR прежнюю целевую ветку, когда смену не удалось довести до блокировки.

    Сбой (база недоступна) пишется в лог: повтор доставки тогда сочтёт смену
    уже записанной и создаст прогон без блокировки — head закроет его gate."""
    try:
        with uow as work:
            current = work.merge_requests.get(merge_request_id)
            if current is None:
                return
            work.merge_requests.update(
                replace(current, target_branch=target_branch, updated_at=now)
            )
            work.commit()
    except Exception:
        logger.exception("не удалось вернуть PR %s прежнюю целевую ветку", merge_request_id)


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
