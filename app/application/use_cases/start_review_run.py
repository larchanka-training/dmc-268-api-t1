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
прогон: активный на старой ветке отменяется. Head блокируется после коммита, до постановки
задачи, при смене ветки и при вебхуке на head, где последний прогон `failed` или `cancelled`
(#309): так повтор после отказа блокировки блокирует снова без отката ветки. Если вместе с
блокировкой не записался и перевод прогона в `failed`, прогон остаётся `queued`, и повтор
получает его дублем — тогда приём переставляет блокировку по нему best-effort, а прогон
выметет sweep. Это бывает только у вебхука: ручной запуск ставит gate при захвате прогона
(#368).

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

from app.application.merge_gate_publish import settle_merge_gate
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
from app.domain.enums import (
    RETRYABLE_STATUSES,
    TERMINAL_STATUSES,
    ReviewRunStatus,
    TriggerSource,
)
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
    `failure` — только у вебхука: head не удалось заблокировать (смена целевой ветки или повтор
    на head, где последний прогон `failed` или `cancelled`), прогон переведён в `failed`.
    Кроме `failure`, заполнены `run`, `merge_request` и `repository` —
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

    `vcs` нужен блокировке head до постановки задачи — у вебхука при смене целевой ветки и при
    повторе на head, где последний прогон `failed` или `cancelled`, — и переблокировке head
    повтором, получившим дублем застрявший `queued` прогон. Сбой постановки — прогон в
    `failed` (`run_outcome` пишет переход), исключение наверх.
    """
    retarget = False
    lock_head = False
    duplicate: RunStart | None = None
    # PR, чей head повтор доставки переблокирует по застрявшему `queued` прогону.
    relock_merge_request_id: UUID | None = None
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
            # Check на head описывает чужой дифф (смена ветки) или упавшее ревью (последний
            # прогон `failed` или `cancelled` — то же правило, что у дубля): до нового ревью
            # head блокируется (#309). Признак — до вставки нового прогона, поэтому повтор после
            # отказа блокировки видит упавший прогон и блокирует снова, даже когда целевая
            # ветка уже записана новой. Завершённый ручной прогон не блокирует: его check
            # описывает тот же дифф. Ручной запуск gate ставит при захвате прогона (#368), а
            # `failure` приёма не обрабатывает.
            latest = work.review_runs.find_latest(existing_merge_request.id, launch.head_sha)
            lock_head = launch.trigger is TriggerSource.WEBHOOK and (
                retarget or (latest is not None and latest.status in RETRYABLE_STATUSES)
            )
            if duplicate_of is not None:
                # Двойной сбой: блокировка не встала и перевод в `failed` не записался —
                # прогон остался `queued` без задачи до sweep, а на head мог остаться
                # `success` прежнего ревью. Повтор доставки переставляет gate по этому прогону.
                if (
                    launch.trigger is TriggerSource.WEBHOOK
                    and duplicate_of.status is ReviewRunStatus.QUEUED
                    and _earlier_review_on_head(work, existing_merge_request.id, launch.head_sha)
                ):
                    relock_merge_request_id = existing_merge_request.id
                duplicate = RunStart(
                    kind="duplicate",
                    run=duplicate_of,
                    merge_request=existing_merge_request,
                    repository=repository,
                )
    if duplicate is not None:
        if relock_merge_request_id is not None:
            _relock_head(uow, vcs, launch, relock_merge_request_id, repository.id)
        return duplicate

    with uow as work:
        if retarget and existing_merge_request is not None:
            # Прогон на старой ветке ещё идёт — выставил бы gate по диффу, которого
            # в PR уже нет. Отмена — своим коммитом до вставки нового прогона:
            # частичный индекс допускает один активный прогон на коммит.
            supersede(work, existing_merge_request.id, launch.head_sha, now())

        merge_request = upsert_merge_request(
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
            merge_request = upsert_merge_request(
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

    if lock_head:
        # Check run висит на коммите: до ревью нового диффа на нём остался бы
        # `success` старого прогона или `failure` упавшего. Блокировка (`in_progress` нового
        # прогона) — после коммита отмены и нового прогона: воркер старого прогона и пересчёт
        # по треду, выставив gate, перечитывают прогон за head, так что любая перестановка с
        # ними кончается gate нового. Задача — после блокировки: новый прогон не выставит
        # gate раньше неё.
        try:
            settle_merge_gate(
                uow,
                vcs,
                merge_request_id=merge_request.id,
                repo_full_name=launch.repository_full_name,
                head_sha=launch.head_sha,
                installation_id=launch.installation_id,
                repository_id=repository.id,
            )
        except VcsError as exc:
            # 502: прогон — в `failed`, задачи нет. Повтор доставки (#149) дублем его не
            # считает, видит упавший прогон на head и блокирует head снова — ветку откатывать
            # не нужно (#309). Не запишется и `failed` — прогон останется `queued`, повтор
            # получит его дублем и переставит блокировку best-effort, а прогон выметет sweep.
            logger.warning(
                "merge gate %s@%s не заблокирован до повторного ревью",
                launch.repository_full_name,
                launch.head_sha,
                exc_info=True,
            )
            _fail_unenqueued(uow, run.id, exc, now(), run_outcome)
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
        _settle_unenqueued(uow, vcs, launch, merge_request.id, repository.id)
        raise
    return RunStart(kind="success", run=run, merge_request=merge_request, repository=repository)


def _earlier_review_on_head(work: UnitOfWork, merge_request_id: UUID, head_sha: str) -> bool:
    """На head есть завершённое ревью — его `success` мог остаться на коммите."""
    return (
        work.review_runs.find_reviewed(merge_request_id, head_sha) is not None
        or work.review_runs.find_completed_manual(merge_request_id, head_sha) is not None
    )


def _relock_head(
    uow: UnitOfWork,
    vcs: VcsGateway,
    launch: RunLaunch,
    merge_request_id: UUID,
    repository_id: UUID,
) -> None:
    """Best-effort: выставить gate по застрявшему `queued` прогону — `in_progress`.

    Сбой GitHub ответ не меняет: повтор остаётся дублем, прогон выметет sweep."""
    try:
        settle_merge_gate(
            uow,
            vcs,
            merge_request_id=merge_request_id,
            repo_full_name=launch.repository_full_name,
            head_sha=launch.head_sha,
            installation_id=launch.installation_id,
            repository_id=repository_id,
        )
    except VcsError:
        logger.warning(
            "merge gate %s@%s не переставлен по застрявшему прогону",
            launch.repository_full_name,
            launch.head_sha,
            exc_info=True,
        )


def supersede(work: UnitOfWork, merge_request_id: UUID, head_sha: str, now: datetime) -> None:
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


def _settle_unenqueued(
    uow: UnitOfWork,
    vcs: VcsGateway,
    launch: RunLaunch,
    merge_request_id: UUID,
    repository_id: UUID,
) -> None:
    """Gate head прогона, не ушедшего в брокер: `failure` вместо `in_progress` блокировки
    смены ветки (#295). Best-effort: сбой пишется в лог и не подменяет ошибку очереди."""
    try:
        settle_merge_gate(
            uow,
            vcs,
            merge_request_id=merge_request_id,
            repo_full_name=launch.repository_full_name,
            head_sha=launch.head_sha,
            installation_id=launch.installation_id,
            repository_id=repository_id,
        )
    except Exception:
        logger.exception(
            "задача не поставлена, а check run merge gate на %s не выставлен", launch.head_sha
        )


def _find_survivor(
    work: UnitOfWork, repository_id: UUID, pr_number: int, head_sha: str
) -> tuple[ReviewRun | None, MergeRequest | None]:
    """Записи победителя гонки после отката: merge_request по естественному
    ключу, активный прогон — по нему и своему коммиту."""
    survivor_mr = work.merge_requests.find_by_number(repository_id, pr_number)
    if survivor_mr is None:
        return None, None
    return work.review_runs.find_active(survivor_mr.id, head_sha), survivor_mr


def upsert_merge_request(
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
