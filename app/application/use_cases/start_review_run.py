"""Общая половина вебхука и ручного запуска: записать прогон и поставить задачу (D1 change
`manual-review-launch`).

Вызывающий уже нашёл репозиторий и достал свежие метаданные PR; здесь — дубль, запись
merge_request и прогона, коммит, разбор гонки и постановка задачи. Правило дубля — у
вызывающего (`find_duplicate`): вебхук считает дублем и завершённый прогон по вебхуку, ручной
запуск — только активный.

Порядок «коммит, потом задача» — по воркеру #36: тот рассчитывает, что прогон
закоммичен до постановки задачи, и сообщение без строки кладёт в DLQ. Сбой
постановки после коммита компенсируется сразу (`_fail_unenqueued`): прогон
переводится в `failed` — `failed` не блокирует следующий запуск ни через
`find_active`, ни через `find_reviewed`, и следующая доставка или запуск создаёт новый прогон.
Оставленный `queued` без задачи сделал бы коммит навсегда без ревью: повтор
до sweep получал бы `duplicate` и задачу больше не ставил. Если не
удалась и компенсация, прогон выметет sweep обработки (`list_unfinished`, #36).

Смена целевой ветки (`edited` с другой веткой, чем записана у PR, #222) даёт новый
прогон: активный на старой ветке отменяется, а head блокируется после коммита, до
постановки задачи. Это бывает только у вебхука: у ручного запуска `action = manual`.

Гонку параллельных запусков страхуют частичные уникальные индексы — отказ любого из них
адаптер хранилища переводит в `ActiveRunConflict`. Проигравший после отката
ищет записи победителя по естественному ключу (репозиторий, номер): у
победителя с тем же коммитом есть активный прогон — исход `conflict`; у победителя
с другим коммитом (opened и synchronize в окне гонки) есть только merge_request —
запись повторяется поверх его записей один раз: update его merge_request и новый
прогон на свой коммит. Второй отказ индекса — исключение наверх (5xx).
Дубль проверяется до upsert'а merge_request: на PR один активный прогон,
повтор ничего не переписывает.

Время и идентификаторы приходят аргументами (`now`, `new_id`): тест проверяет точный
timestamp и идентификатор.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.application.merge_gate_publish import build_retarget_check
from app.application.ports.job_queue import JobQueue
from app.application.ports.run_outcome import NO_RUN_OUTCOME, RunOutcomeRecorder
from app.application.ports.unit_of_work import ActiveRunConflict, UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.domain.entities import (
    MergeRequest,
    PRMetadata,
    Repository,
    ReviewJob,
    ReviewRun,
)
from app.domain.enums import TERMINAL_STATUSES, ReviewRunStatus, TriggerSource
from app.domain.lifecycle import advance
from app.domain.vcs_errors import VcsError

logger = logging.getLogger(__name__)

# Действие вебхука, которое может сменить целевую ветку PR (#222).
_RETARGET_ACTION = "edited"

# Правило дубля вызывающего: прогон, повтором которого считается запуск; `None` — дубля нет.
FindDuplicate = Callable[[UnitOfWork, UUID, str], ReviewRun | None]


@dataclass(frozen=True, slots=True)
class RunLaunch:
    """Что запускается: источник, действие для сообщения очереди и PR на коммите.

    `action` уходит в `ReviewJob.action` (у вебхука — действие события, у ручного запуска —
    `manual`); `installation_id` — установка приложения, от имени которой воркер достанет дифф.
    """

    trigger: TriggerSource
    action: str
    pr_number: int
    head_sha: str
    installation_id: int
    repository_full_name: str
    repository_provider_id: str


@dataclass(frozen=True, slots=True)
class RunStart:
    """Исход записи прогона.

    `duplicate` — на коммит уже есть прогон по правилу вызывающего, найденный до вставки и до
    обновления merge_request. `conflict` — гонку отклонил уникальный индекс, проигравший
    откатился и нашёл активный прогон победителя. `success` — прогон записан, задача поставлена.
    `failure` — только смена целевой ветки у вебхука: head не удалось заблокировать, прогон
    переведён в `failed`. Кроме `failure`, заполнены `run`, `merge_request` и `repository` —
    из них HTTP-слой собирает проекцию `ReviewJob`.
    """

    kind: Literal["duplicate", "conflict", "success", "failure"]
    run: ReviewRun | None = None
    merge_request: MergeRequest | None = None
    repository: Repository | None = None


def start_review_run(
    uow: UnitOfWork,
    queue: JobQueue,
    vcs: VcsGateway,
    repository: Repository,
    launch: RunLaunch,
    metadata: PRMetadata,
    *,
    find_duplicate: FindDuplicate,
    now: Callable[[], datetime],
    new_id: Callable[[], UUID],
    run_outcome: RunOutcomeRecorder = NO_RUN_OUTCOME,
) -> RunStart:
    """Записать прогон на `launch.head_sha` и поставить задачу.

    `vcs` нужен только смене целевой ветки: блокировка head до постановки задачи.
    Сбой постановки — прогон в `failed` (`run_outcome` пишет переход), исключение наверх.
    """
    retarget = False
    with uow as work:
        existing_merge_request = work.merge_requests.find_by_number(repository.id, launch.pr_number)
        if existing_merge_request is not None:
            # Смена целевой ветки (#222) — только `edited` с другой веткой, чем
            # записана у PR. Вершина ветки (`base_sha`) двигается и без смены
            # ветки — от неё повтор `synchronize` дублем быть не перестаёт.
            retarget = (
                launch.action == _RETARGET_ACTION
                and existing_merge_request.target_branch != metadata.target_branch
            )
            duplicate_of = None if retarget else find_duplicate(
                work, existing_merge_request.id, launch.head_sha
            )
            if duplicate_of is not None:
                return RunStart(
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
            _supersede(work, existing_merge_request.id, launch.head_sha, now())

        merge_request = _upsert_merge_request(
            work, repository.id, existing_merge_request, launch, metadata, now(), new_id
        )
        run = _create_review_run(work, merge_request.id, launch, metadata, now(), new_id())
        try:
            work.commit()
        except ActiveRunConflict:
            # Индекс сказал, что победитель закоммитил раньше: либо активный
            # прогон на этот коммит, либо merge_request этого PR. Наша вставка
            # откатилась, поэтому записи победителя ищутся по естественному
            # ключу (репозиторий, номер), а не по идентификатору нашей вставки.
            work.rollback()
            survivor_run, survivor_mr = _find_survivor(
                work, repository.id, launch.pr_number, launch.head_sha
            )
            if survivor_run is not None:
                return RunStart(
                    kind="conflict",
                    run=survivor_run,
                    merge_request=survivor_mr,
                    repository=repository,
                )
            if survivor_mr is None:
                # Ни merge_request, ни активного прогона на наш коммит нет —
                # это не гонка, отказ индекса означает настоящую ошибку данных.
                raise
            # Победитель нёс другой коммит (opened и synchronize в окне
            # гонки): его merge_request уже есть, а активного прогона на наш
            # head_sha нет. Запись повторяется поверх его записей: update
            # его merge_request и новый прогон на наш коммит.
            merge_request = _upsert_merge_request(
                work, repository.id, survivor_mr, launch, metadata, now(), new_id
            )
            run = _create_review_run(work, merge_request.id, launch, metadata, now(), new_id())
            try:
                work.commit()
            except ActiveRunConflict:
                # Между откатом и повтором активный прогон на наш коммит
                # создал третий запуск — возвращаем его прогон. Любой
                # другой повторный отказ — наверх: 5xx. Сам GitHub упавшую
                # доставку не повторяет: её переотправляет проход воркера (#149).
                work.rollback()
                survivor_run, survivor_mr = _find_survivor(
                    work, repository.id, launch.pr_number, launch.head_sha
                )
                if survivor_run is None or survivor_mr is None:
                    raise
                return RunStart(
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
                launch.repository_full_name,
                launch.head_sha,
                launch.installation_id,
                build_retarget_check(),
            )
        except VcsError as exc:
            # 502 и откат: прогон — в `failed` (повтор доставки он дублем не
            # делает), PR — на прежнюю ветку, чтобы повтор (#149) снова увидел
            # смену и заблокировал head.
            logger.warning(
                "merge gate %s@%s не заблокирован после смены целевой ветки",
                launch.repository_full_name,
                launch.head_sha,
                exc_info=True,
            )
            _fail_unenqueued(uow, run.id, exc, now(), run_outcome)
            if existing_merge_request is not None:
                _restore_target_branch(
                    uow, merge_request.id, existing_merge_request.target_branch, now()
                )
            return RunStart(kind="failure")

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
                action=launch.action,
                installation_id=launch.installation_id,
                repository_provider_id=launch.repository_provider_id,
                repository_full_name=launch.repository_full_name,
                pull_request_number=launch.pr_number,
                head_sha=launch.head_sha,
                base_sha=metadata.base_sha,
            )
        )
    except Exception as exc:
        # «Закоммитили, но не поставили»: оставшийся queued блокировал бы
        # коммит от ревью навсегда. Прогон переводится в failed: терминальное
        # состояние не блокирует следующий запуск. Ошибка идёт дальше — 5xx.
        _fail_unenqueued(uow, run.id, exc, now(), run_outcome)
        raise
    return RunStart(kind="success", run=run, merge_request=merge_request, repository=repository)


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
    uow: UnitOfWork,
    run_id: UUID,
    cause: Exception,
    now: datetime,
    run_outcome: RunOutcomeRecorder,
) -> None:
    """Перевести закоммиченный прогон в failed, когда задача не ушла в брокер.

    Отказ компенсации (база недоступна) не маскирует исходную ошибку —
    прогон останется до sweep обработки (#36). Успешный перевод —
    терминальный переход, его пишет `run_outcome` после коммита: счётчик
    считает прогоны, а не обработки сообщений.
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
            run_outcome.run_finished(ReviewRunStatus.FAILED.value)
    except Exception:
        # Идентификатор — атрибутом записи, не текстом (правило «Логи»):
        # контекста сообщения воркера здесь нет, поэтому его несёт
        # extra; имя — то же, что у атрибута спанов.
        logger.exception(
            "не удалось перевести прогон в failed после сбоя очереди",
            extra={"review_run_id": str(run_id)},
        )


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
            work.merge_requests.update(replace(current, target_branch=target_branch, updated_at=now))
            work.commit()
    except Exception:
        logger.exception("не удалось вернуть PR %s прежнюю целевую ветку", merge_request_id)


def _find_survivor(
    work: UnitOfWork, repository_id: UUID, pr_number: int, head_sha: str
) -> tuple[ReviewRun | None, MergeRequest | None]:
    """Записи победителя гонки после отката: merge_request по естественному
    ключу, активный прогон — по нему и своему коммиту."""
    survivor_mr = work.merge_requests.find_by_number(repository_id, pr_number)
    if survivor_mr is None:
        return None, None
    return work.review_runs.find_active(survivor_mr.id, head_sha), survivor_mr


def _upsert_merge_request(
    work: UnitOfWork,
    repository_id: UUID,
    existing: MergeRequest | None,
    launch: RunLaunch,
    metadata: PRMetadata,
    now: datetime,
    new_id: Callable[[], UUID],
) -> MergeRequest:
    """Обновить найденный PR свежими метаданными VCS или создать новый.

    Заголовок, автор и ветки берутся из свежих метаданных VCS: они приезжают тем же
    запросом, что и `base_sha`, и актуальнее того, что успела доставить очередь вебхуков.
    `head_sha` — из `launch`: прогон создаётся для запускаемого коммита (у вебхука —
    доставленного). Вызывается только когда дубля нет: обновление без будущего коммита
    откатилось бы вместе с транзакцией.
    """
    if existing is None:
        created = MergeRequest(
            id=new_id(),
            repository_id=repository_id,
            number=launch.pr_number,
            title=metadata.title,
            description="",
            author=metadata.author,
            source_branch=metadata.source_branch,
            target_branch=metadata.target_branch,
            head_sha=launch.head_sha,
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
        head_sha=launch.head_sha,
        state=metadata.state,
        updated_at=now,
    )
    work.merge_requests.update(updated)
    return updated


def _create_review_run(
    work: UnitOfWork,
    merge_request_id: UUID,
    launch: RunLaunch,
    metadata: PRMetadata,
    now: datetime,
    run_id: UUID,
) -> ReviewRun:
    """Создать прогон в состоянии queued с источником из `launch`."""
    run = ReviewRun(
        id=run_id,
        merge_request_id=merge_request_id,
        head_sha=launch.head_sha,
        base_sha=metadata.base_sha,
        status=ReviewRunStatus.QUEUED,
        trigger=launch.trigger,
        last_progress_at=now,
        created_at=now,
        updated_at=now,
    )
    work.review_runs.add(run)
    return run
