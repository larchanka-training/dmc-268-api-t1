"""Check run merge gate по открытым тредам не ниже порога репозитория."""

import logging
from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.vcs_review import MergeGateCheck
from app.domain.entities import Finding, Repository, ReviewRun
from app.domain.enums import FindingSeverity, ReviewRunStatus, TriggerSource
from app.domain.merge_gate import merge_blocked_by_findings, untracked_findings
from app.domain.vcs_errors import VcsError

logger = logging.getLogger(__name__)

# Поправку gate отправляет только тот, кто увидел смену итога, и второй
# возможности у неё нет: после сбоя на head остаётся устаревший итог.
# Адаптер сам повторяет 5xx и лимит; здесь - ещё попытки после его отказа.
SETTLE_ATTEMPTS = 3


def collect_open_findings(
    uow: UnitOfWork, merge_request_id: UUID, *, head_run_id: UUID | None
) -> tuple[Finding, ...]:
    """Открытые треды PR и находки прогона на head, чей id inline не получен.

    `head_run_id` - прогон, чьё ревью стоит на текущем head; `None` - такого нет.
    """
    with uow:
        return _open_findings(uow, merge_request_id, head_run_id)


def _open_findings(
    uow: UnitOfWork, merge_request_id: UUID, head_run_id: UUID | None
) -> tuple[Finding, ...]:
    threads = uow.published_comments.list_open_threads_for_merge_request(merge_request_id)
    untracked: tuple[Finding, ...] = ()
    if head_run_id is not None:
        untracked = untracked_findings(
            uow.findings.list_for_run(head_run_id),
            uow.published_comments.list_for_run(head_run_id),
        )
    return tuple(t.finding for t in threads) + untracked


# Выключенный репозиторий (`DELETE /repositories/{id}`): ревью нет, но обязательная проверка
# в ruleset без check run держала бы каждый новый PR в «Expected — Waiting for status».
DISABLED_REVIEW_CHECK = MergeGateCheck(
    conclusion="success",
    title="Ревью выключено в сервисе",
    summary="Ревью этого репозитория выключено в сервисе: проверка мёрж не блокирует.",
)

# Включённый репозиторий с `auto_review_enabled = false`: тот же путь, что у выключенного, но
# свой текст — включается настройкой в кабинете, а не повторным подключением (design D5).
AUTO_REVIEW_OFF_CHECK = MergeGateCheck(
    conclusion="success",
    title="Авторевью выключено в настройках",
    summary=(
        "Автоматическое ревью выключено в настройках репозитория в кабинете; "
        "проверка мёрж не блокирует."
    ),
)

def pause_check(repository: Repository, head_run: ReviewRun | None) -> MergeGateCheck | None:
    """Check run на head, если ревью на нём не идёт; `None` — идёт, gate ставит прогон.

    `head_run` — прогон, отвечающий за head (`head_run_in_charge`), `None` — прогона нет.
    Выключение репозитория важнее настройки и ручного прогона: при нём всегда
    `DISABLED_REVIEW_CHECK`, чтобы пользователь чинил то, что действительно мешает
    (сначала подключение), а начатый до выключения прогон не блокировал мердж.
    Пауза `auto_review_enabled = false` — про автоматическое ревью: ручной прогон на head,
    пока он не упал и не отменён, ставит свой gate (D11); после упавшего повторного —
    завершённый прежний, его подставляет `head_run_in_charge`. Упавший ручной прогон без
    завершённого до него — check паузы: `failure` держал бы PR, который авторевью не ждёт.
    Вызывают вебхук PR, вебхук треда, `settle_merge_gate` и упавший прогон.
    """
    if repository.connected_at is None:
        return DISABLED_REVIEW_CHECK
    if not repository.auto_review_enabled:
        if _manual_run_in_charge(head_run):
            return None
        return AUTO_REVIEW_OFF_CHECK
    return None


def head_run_in_charge(
    uow: UnitOfWork, repository: Repository, merge_request_id: UUID, head_sha: str
) -> ReviewRun | None:
    """Прогон, чей итог отвечает за gate head; зовётся внутри открытого вызывающим `with uow`.

    Обычно — последний прогон на head (`find_latest`). На паузе авторевью, если последний
    не ручной в работе или завершённый, — последний завершённый ручной прогон на head, а
    его нет — последний: упавший или отменённый повтор ручного ревью не снимает gate
    прежнего (change `manual-gate-after-failed-relaunch`, D1). Вытеснен ли прогон новым,
    решает `find_latest`, а не эта функция.
    """
    latest = uow.review_runs.find_latest(merge_request_id, head_sha)
    paused = repository.connected_at is not None and not repository.auto_review_enabled
    if not paused or _manual_run_in_charge(latest):
        return latest
    return uow.review_runs.find_completed_manual(merge_request_id, head_sha) or latest


def publish_pause_check(
    uow: UnitOfWork,
    vcs: VcsGateway,
    check: MergeGateCheck,
    *,
    repository_id: UUID,
    pr_number: int,
    repo_full_name: str,
    head_sha: str,
    installation_id: int,
) -> None:
    """Выставить check паузы `check` на head и согласовать его с прогоном, ставшим за head.

    Пауза решена по прогону, прочитанному до POST. Пока GitHub принимал check, ручной
    прогон на паузе авторевью (D11) мог дойти до публикации и выставить свой gate —
    задержанный check паузы перекрыл бы его `failure`. Поэтому после POST, и после сбоя
    тоже (сбой не значит, что POST не дошёл), прогон и репозиторий перечитываются: если
    пауза больше не действует, а последний прогон уже публикуется или завершён, его gate
    выставляет `settle_merge_gate`. Прогон до публикации gate выставит сам, позже нашего
    POST. Прогон за head — `head_run_in_charge`: после упавшего повторного ручного прогона
    это завершённый прежний. `VcsError` POST паузы или поправки — вызывающему, после поправки.
    """
    error: VcsError | None = None
    try:
        vcs.set_merge_gate_check(repo_full_name, head_sha, installation_id, check)
    except VcsError as exc:
        error = exc
    with uow:
        repository = uow.repositories.get(repository_id)
        merge_request = uow.merge_requests.find_by_number(repository_id, pr_number)
        latest = (
            head_run_in_charge(uow, repository, merge_request.id, head_sha)
            if repository is not None and merge_request is not None
            else None
        )
    if (
        repository is not None
        and merge_request is not None
        and latest is not None
        and latest.status in {ReviewRunStatus.PUBLISHING, ReviewRunStatus.COMPLETED}
        and pause_check(repository, latest) is None
    ):
        logger.info(
            "check паузы %s@%s устарел: за head отвечает прогон %s",
            repo_full_name,
            head_sha,
            latest.id,
        )
        settle_merge_gate(
            uow,
            vcs,
            merge_request_id=merge_request.id,
            repo_full_name=repo_full_name,
            head_sha=head_sha,
            installation_id=installation_id,
            run_id=latest.id,
            repository_id=repository_id,
        )
    if error is not None:
        raise error


def _manual_run_in_charge(head_run: ReviewRun | None) -> bool:
    return (
        head_run is not None
        and head_run.trigger is TriggerSource.MANUAL
        and head_run.status not in {ReviewRunStatus.FAILED, ReviewRunStatus.CANCELLED}
    )


def build_merge_gate_check(
    open_findings: tuple[Finding, ...], min_severity: FindingSeverity
) -> MergeGateCheck:
    """Check run merge gate: заголовок и текст называют порог, и при `critical` тоже (design D4)."""
    blocked = merge_blocked_by_findings(open_findings, min_severity)
    threshold = min_severity.value
    if blocked:
        return MergeGateCheck(
            conclusion="failure",
            title=f"Есть незакрытые замечания уровня {threshold} и выше",
            summary=(
                f"Остаются незакрытые замечания уровня {threshold} и выше — мердж заблокирован."
            ),
        )
    return MergeGateCheck(
        conclusion="success",
        title="Ревью пройдено",
        summary=f"Незакрытых замечаний уровня {threshold} и выше нет.",
    )


def build_retarget_check() -> MergeGateCheck:
    """Блокировка head после смены целевой ветки PR, до ревью нового диффа (#222).

    `failure`, а не `in_progress`: если новое ревью упадёт, блокировка остаётся,
    а прежний `success` по старому диффу не возвращается.
    """
    return MergeGateCheck(
        conclusion="failure",
        title="Целевая ветка сменилась — идёт повторное ревью",
        summary="Прежнее ревью проверяло дифф против другой ветки. Мердж заблокирован до ревью нового диффа.",
    )


def build_failed_review_check() -> MergeGateCheck:
    """Блокировка head, чей последний прогон упал (ревью #225).

    Прогон мог упасть после блокировки смены ветки, а её POST - не дойти: тогда
    на head остался бы `success` прежнего прогона по старому диффу.
    """
    return MergeGateCheck(
        conclusion="failure",
        title="Ревью не удалось",
        summary="Последнее ревью этого коммита упало. Мердж заблокирован до успешного ревью.",
    )


def settle_merge_gate(
    uow: UnitOfWork,
    vcs: VcsGateway,
    *,
    merge_request_id: UUID,
    repo_full_name: str,
    head_sha: str,
    installation_id: int,
    run_id: UUID,
    repository_id: UUID,
) -> None:
    """Выставить gate прогона `run_id` - по его открытым находкам и порогу
    `min_severity` репозитория `repository_id` - или, если тот уже не отвечает за head,
    gate того, кто отвечает (#222).

    Гонка со сменой целевой ветки: приём сначала коммитит отмену старого прогона и
    новый прогон, потом выставляет блокировку. Gate собирается по последнему
    прогону на head и после каждого POST перечитывается: если приём уже
    закоммитил, здесь видна смена и gate переписывается; если нет - блокировка
    приёма придёт позже и перекроет `success`. Так любая перестановка кончается
    gate актуального прогона. После сбоя `SETTLE_ATTEMPTS` попыток - `VcsError`
    вызывающему. Находки и порог читаются здесь же, на каждой попытке: порог могли
    сменить, пока шёл прогон или сбой, и gate завершённого чужого прогона по старому
    порогу снял бы его блокировку. На паузе ревью (`pause_check`) выставляется check
    паузы: `failure` завершившегося прогона заблокировал бы PR, который ревью не ждёт.
    Ручной прогон на паузе авторевью ставит свой gate (D11); прогон за head берётся через
    `head_run_in_charge`, поэтому после упавшего повторного ручного прогона gate — по
    находкам завершённого прежнего.
    """
    failures = 0
    # Последний выставленный check: пока GitHub его принимал, итог мог измениться из-за
    # любого входа - прогона, порога, тредов, паузы (#325). Сравниваем сам check.
    settled: MergeGateCheck | None = None
    while True:
        # Перед каждой попыткой - заново: за время сбоя за head мог начать
        # отвечать другой прогон, и повтор прежнего check вернул бы чужой итог.
        with uow:
            repository = uow.repositories.get(repository_id)
            latest = (
                head_run_in_charge(uow, repository, merge_request_id, head_sha)
                if repository is not None
                else None
            )
        if repository is None:
            raise LookupError(f"repository {repository_id} not found")
        if latest is None:
            return
        pause = pause_check(repository, latest)
        if pause is not None:
            current = pause
        elif (latest.id == run_id and latest.status is not ReviewRunStatus.CANCELLED) or (
            latest.status is ReviewRunStatus.COMPLETED
        ):
            # Свой прогон — или новый, успевший завершиться: его gate мог уйти раньше
            # нашего POST. Gate — по открытым находкам этого прогона и порогу репозитория.
            current = build_merge_gate_check(
                collect_open_findings(uow, merge_request_id, head_run_id=latest.id),
                repository.min_severity,
            )
        elif latest.status is ReviewRunStatus.FAILED:
            # Новый прогон уже упал: повторного ревью не будет, head ждёт push.
            current = build_failed_review_check()
        else:
            current = build_retarget_check()
        if current == settled:
            return
        if latest.id != run_id:
            logger.info(
                "merge gate %s@%s переписан: прогон %s больше не отвечает за head, последний - %s",
                repo_full_name,
                head_sha,
                run_id,
                latest.id,
            )
        try:
            vcs.set_merge_gate_check(repo_full_name, head_sha, installation_id, current)
        except VcsError:
            failures += 1
            if failures == SETTLE_ATTEMPTS:
                raise
            logger.warning(
                "merge gate %s@%s не выставлен, попытка %d из %d",
                repo_full_name,
                head_sha,
                failures,
                SETTLE_ATTEMPTS,
                exc_info=True,
            )
            # Сбой не значит, что POST не дошёл: что на head, неизвестно, повтор обязателен.
            settled = None
            continue
        settled = current
