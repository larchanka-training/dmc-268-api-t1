"""Use case приёма вебхука GitHub: от события до задачи в очереди.

Оркестрирует чистые функции домена и порты: извлекает событие из payload'а,
отсекает чужие PR (`is_trusted_pull_request`; отказ — `ignored` и запись в лог), ищет
зарегистрированный репозиторий, достаёт метаданные PR через `VcsGateway` (сбой —
502 ещё до создания записей) и создаёт `ReviewRun`.
Дифф use case не запрашивает: его размер задаёт автор PR, а GitHub ждёт ответа
на доставку 10 секунд. Дифф с лимитом размера достаёт и разбирает воркер
(design D3) по идентификаторам из задачи (#144).
Зависимости — только порты (`UnitOfWork`, `VcsGateway`, `JobQueue`),
поэтому юнит-тесты обходятся фейками без базы, брокера и сети.

Транзакции короткие: репозиторий ищется отдельным чтением, VCS-вызовы идут
между транзакциями — соединение пула не висит idle in transaction, пока
отвечает GitHub. Запись — вторая транзакция, одна на merge_request и прогон.
Запись, разбор гонки, постановка и компенсация — общие с ручным запуском,
`start_review_run` (`app/application/use_cases/start_review_run.py`, D1 change
`manual-review-launch`); здесь — разбор события, пауза, метаданные и правило дубля.

Повторная доставка на тот же коммит не создаёт второй прогон: `find_active`
находит существующий активный, `find_reviewed` — завершённый не-failed прогон
по вебхуку (исход `duplicate` в обоих случаях, #75), если последний прогон на
коммит не упал и не отменён. Таблицы ключей `X-GitHub-Delivery` нет: повтор после
нашего сбоя постановки приходит с тем же ключом и был бы отброшен, а так `failed`
прогон (компенсация в `start_review_run`) повтор не блокирует. Смена целевой ветки
(`edited` с другой веткой, чем записана у PR, #222) даёт новый прогон.

Гонка параллельных доставок разбирается в `start_review_run`; вебхуку от неё остаются
исходы: `conflict` — 202 с проекцией победителя, второй отказ индекса — 5xx, и доставку
переотправит проход воркера (`redeliver_failed_webhooks`, #149).

Время и идентификаторы приходят аргументами (`now`, `new_id`), а не читаются
из часов или генератора: тест проверяет точный timestamp и идентификатор.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from app.application.merge_gate_publish import (
    head_run_in_charge,
    pause_check,
    publish_pause_check,
)
from app.application.ports.job_queue import JobQueue
from app.application.ports.run_outcome import NO_RUN_OUTCOME, RunOutcomeRecorder
from app.application.ports.unit_of_work import ActiveRunConflict, UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.vcs_review import MergeGateCheck
from app.application.use_cases.start_review_run import (
    RunLaunch,
    start_review_run,
    supersede,
    upsert_merge_request,
)
from app.domain.entities import (
    MergeRequest,
    PRMetadata,
    Repository,
    ReviewRun,
    WebhookEvent,
)
from app.domain.enums import (
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.glob import branch_selected
from app.domain.vcs_errors import VcsError
from app.domain.webhook import extract_github_event, is_trusted_pull_request

logger = logging.getLogger(__name__)

# Последний прогон на коммит в этих статусах повтор доставки не блокирует.
_RETRYABLE_STATUSES = frozenset({ReviewRunStatus.FAILED, ReviewRunStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class WebhookOutcome:
    """Результат приёма вебхука: одна из пяти веток.

    `ignored` — событие не запускает ревью (`reopened`, неизвестное действие,
    PR постороннего автора, незарегистрированный или выключенный репозиторий, выключенное
    авторевью, целевая ветка вне `branch_filter`): прогона и задачи нет. У выключенного
    репозитория, при выключенном авторевью и вне фильтра при этом уходит VCS-вызов: check run
    паузы на head («Ревью выключено в сервисе», «Авторевью выключено в настройках» или «Ветка
    не в фильтре ревью»); вне фильтра запись PR ещё и обновляется. `failure` —
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
    run_outcome: RunOutcomeRecorder = NO_RUN_OUTCOME,
) -> WebhookOutcome:
    """Принять вебхук: от payload'а до закоммиченного прогона и задачи.

    `now` и `new_id` передаются вызывающим, чтобы тесты фиксировали точные
    значения. Вне тестов это `datetime.now(UTC)` и `app.domain.ids.new_id`.
    `run_outcome` пишет терминальный переход компенсации `_fail_unenqueued`
    в счётчик прогонов: без записи алерт по доле упавших слеп при падении
    брокера — пайплайн и выметатель этот переход не делают.
    """
    event = extract_github_event(payload)
    if event is None:
        return WebhookOutcome(kind="ignored")
    if not is_trusted_pull_request(event.from_fork, event.author_association):
        logger.info(
            "PR не ревьюится: автор не свой",
            extra={
                "repo_full_name": event.repo_full_name,
                "pr_number": event.pr_number,
                "author_association": event.author_association,
            },
        )
        return WebhookOutcome(kind="ignored")

    # Короткое чтение: транзакция живёт только на SELECT репозитория и прогона на head.
    with uow as work:
        repository = work.repositories.find_by_provider(
            Provider.GITHUB, event.repo_provider_id
        )
        head_run = _latest_run_on_head(work, repository, event) if repository else None
    if repository is None:
        return WebhookOutcome(kind="ignored")
    # Выключенный (`DELETE /repositories/{id}`) или с `auto_review_enabled = false`: запись
    # есть, а ревью не нужно. Прогона и задачи нет, но на head ставится `success` по причине
    # паузы, иначе обязательная проверка в ruleset держала бы PR в «Expected — Waiting for
    # status». Ручной прогон на этом head ставит свой gate и на паузе авторевью (D11), в том
    # числе завершённый, чей повтор упал; на новом коммите прогона нет, и ставится check паузы.
    # Фильтр веток сверяется ниже, по свежей целевой ветке из метаданных.
    check = pause_check(repository, head_run, None)
    if check is not None:
        _pass_merge_gate_of_paused(uow, vcs, repository, event, check)
        return WebhookOutcome(kind="ignored")
    if not repository.auto_review_enabled:
        # Пауза, но head держит ручной прогон: gate его, а автоматического ревью нет.
        # Дублем событие не признать — `find_reviewed` видит только вебхуковые прогоны.
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

    launch = RunLaunch(
        trigger=TriggerSource.WEBHOOK,
        action=event.action,
        pr_number=event.pr_number,
        head_sha=event.head_sha,
        installation_id=event.installation_id,
        repository_full_name=event.repo_full_name,
        repository_provider_id=event.repo_provider_id,
    )
    # Свежая ветка, а не из записи PR: `edited` мог увести PR из фильтра или вернуть в него.
    if not branch_selected(metadata.target_branch, repository.branch_filter):
        _skip_filtered_branch(uow, vcs, repository, event, launch, metadata, now, new_id)
        return WebhookOutcome(kind="ignored")

    start = start_review_run(
        uow,
        queue,
        vcs,
        repository,
        launch,
        metadata,
        find_duplicate=_duplicate_of,
        now=now,
        new_id=new_id,
        run_outcome=run_outcome,
    )
    return WebhookOutcome(
        kind=start.kind,
        run=start.run,
        merge_request=start.merge_request,
        repository=start.repository,
    )


def _latest_run_on_head(
    work: UnitOfWork, repository: Repository, event: WebhookEvent
) -> ReviewRun | None:
    """Прогон, отвечающий за gate head события (`head_run_in_charge`); `None` — PR ещё не
    записан или прогона нет. На паузе авторевью после повторного ручного прогона без ревью это
    завершённый ручной прогон до него. Правило дубля смотрит на `find_latest` (`_duplicate_of`).
    """
    merge_request = work.merge_requests.find_by_number(repository.id, event.pr_number)
    if merge_request is None:
        return None
    return head_run_in_charge(work, repository, merge_request.id, event.head_sha)


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


def _skip_filtered_branch(
    uow: UnitOfWork,
    vcs: VcsGateway,
    repository: Repository,
    event: WebhookEvent,
    launch: RunLaunch,
    metadata: PRMetadata,
    now: Callable[[], datetime],
    new_id: Callable[[], UUID],
) -> None:
    """PR вне `branch_filter`: прогона нет, на head ставится check паузы (#313).

    Запись PR обновляется свежими метаданными, чтобы возврат в фильтр увидел смену ветки
    (#222): head заблокируется до нового ревью, а завершённый прогон на коммите дублем не
    станет. Если PR увели из фильтра, активный прогон на коммите отменяется, потому что он
    ревьюит ветку, которую ревьюить уже не нужно. Ручной прогон, отвечающий за head, ставит
    свой gate, как на паузе авторевью (D11).
    """
    with uow as work:
        existing = work.merge_requests.find_by_number(repository.id, event.pr_number)
        merge_request = upsert_merge_request(
            work, repository.id, existing, launch, metadata, now(), new_id
        )
        if existing is not None and existing.target_branch != metadata.target_branch:
            supersede(work, existing.id, event.head_sha, now())
        try:
            work.commit()
        except ActiveRunConflict:
            # Параллельная доставка того же PR записала его раньше: её запись так же свежа.
            work.rollback()
        head_run = _latest_run_on_head(work, repository, event)
    logger.info(
        "PR не ревьюится: целевая ветка вне branch_filter",
        extra={"repository_id": str(repository.id), "pr_number": event.pr_number},
    )
    check = pause_check(repository, head_run, merge_request.target_branch)
    if check is not None:
        _pass_merge_gate_of_paused(uow, vcs, repository, event, check)


def _pass_merge_gate_of_paused(
    uow: UnitOfWork,
    vcs: VcsGateway,
    repository: Repository,
    event: WebhookEvent,
    check: MergeGateCheck,
) -> None:
    """Check run паузы ревью (`success`) на head; best-effort.

    Сбой GitHub не меняет ответ (`ignored`) и не даёт 502: переотправка доставки ради
    проверки репозитория без ревью не нужна, следующий push поставит её снова.
    Ручной прогон, ставший за head, пока шёл POST, получает свой gate обратно
    (`publish_pause_check`).
    """
    try:
        publish_pause_check(
            uow,
            vcs,
            check,
            repository_id=repository.id,
            pr_number=event.pr_number,
            repo_full_name=event.repo_full_name,
            head_sha=event.head_sha,
            installation_id=event.installation_id,
        )
    except VcsError:
        logger.warning(
            "check run паузы ревью репозитория %s не поставлен", event.repo_full_name, exc_info=True
        )
