"""Check run merge gate по открытым тредам не ниже порога репозитория."""

import logging
from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.vcs_review import MergeGateCheck
from app.domain.entities import Finding, Repository, ReviewRun
from app.domain.enums import (
    CommentKind,
    CoverageReason,
    FindingSeverity,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.glob import branch_selected
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

# Целевая ветка PR вне `branch_filter` репозитория: авторевью её не ревьюит, а обязательная
# проверка без check run держала бы PR в ожидании (#313).
BRANCH_FILTERED_CHECK = MergeGateCheck(
    conclusion="success",
    title="Ветка не в фильтре ревью",
    summary=(
        "Целевая ветка этого PR не подходит под фильтр веток в настройках репозитория; "
        "ревью не идёт, проверка мёрж не блокирует."
    ),
)


def pause_check(
    repository: Repository, head_run: ReviewRun | None, target_branch: str | None
) -> MergeGateCheck | None:
    """Check run на head, если ревью на нём не идёт; `None` — идёт, gate ставит прогон.

    `head_run` — прогон, отвечающий за head (`head_run_in_charge`), `None` — прогона нет.
    Выключение репозитория важнее настройки и ручного прогона: при нём всегда
    `DISABLED_REVIEW_CHECK`, чтобы пользователь чинил то, что действительно мешает
    (сначала подключение), а начатый до выключения прогон не блокировал мердж.
    Пауза `auto_review_enabled = false` — про автоматическое ревью: ручной прогон на head,
    пока он не упал, не отменён и не завершён без денег, ставит свой gate (D11); если последний — упавший или
    отменённый ручной, — завершённый ручной до него, его подставляет `head_run_in_charge`. Упавший ручной прогон без
    завершённого до него — check паузы: `failure` держал бы PR, который авторевью не ждёт.
    Целевая ветка вне `branch_filter` даёт такую же паузу с проверкой `BRANCH_FILTERED_CHECK`;
    пауза авторевью важнее фильтра. `target_branch` берётся из записи PR; `None` передаёт
    вебхук PR до свежих метаданных, фильтр он применяет сам.
    Вызывают вебхук PR, вебхук треда, `settle_merge_gate` и упавший прогон.
    """
    if repository.connected_at is None:
        return DISABLED_REVIEW_CHECK
    if not repository.auto_review_enabled:
        check = AUTO_REVIEW_OFF_CHECK
    elif target_branch is not None and not branch_selected(
        target_branch, repository.branch_filter
    ):
        check = BRANCH_FILTERED_CHECK
    else:
        return None
    return None if _manual_run_in_charge(head_run) else check


def head_run_in_charge(
    uow: UnitOfWork, repository: Repository | None, merge_request_id: UUID, head_sha: str
) -> ReviewRun | None:
    """Прогон, чей итог отвечает за gate head; зовётся внутри открытого вызывающим `with uow`.

    Обычно — последний прогон на head (`find_latest`), и без записи репозитория тоже. На
    паузе авторевью и вне фильтра веток (`pause_check`), если последний — ручной и он упал,
    отменён или завершён без денег, — последний завершённый ручной прогон на head, а его нет — последний:
    упавший или отменённый повтор ручного ревью не снимает gate прежнего (change
    `manual-gate-after-failed-relaunch`, D1).
    Последний вебхуковый за head отвечает сам, даже после ручного: он проверял, возможно,
    другую базу (change `manual-gate-fallback-after-manual-only`). Вытеснен ли прогон
    новым, решает `find_latest`, а не эта функция.
    """
    latest = uow.review_runs.find_latest(merge_request_id, head_sha)
    if repository is None or not _failed_manual_relaunch(uow, repository, merge_request_id, latest):
        return latest
    return uow.review_runs.find_completed_manual(merge_request_id, head_sha) or latest


def _failed_manual_relaunch(
    uow: UnitOfWork, repository: Repository, merge_request_id: UUID, latest: ReviewRun | None
) -> bool:
    if (
        repository.connected_at is None
        or latest is None
        or latest.trigger is not TriggerSource.MANUAL
        or not _no_review(latest)
    ):
        return False
    merge_request = uow.merge_requests.get(merge_request_id)
    target_branch = merge_request.target_branch if merge_request is not None else None
    return pause_check(repository, None, target_branch) is not None


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
            if merge_request is not None
            else None
        )
    if (
        repository is not None
        and merge_request is not None
        and latest is not None
        and latest.status in {ReviewRunStatus.PUBLISHING, ReviewRunStatus.COMPLETED}
        and pause_check(repository, latest, merge_request.target_branch) is None
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
            repository_id=repository_id,
        )
    if error is not None:
        raise error


def _manual_run_in_charge(head_run: ReviewRun | None) -> bool:
    return head_run is not None and head_run.trigger is TriggerSource.MANUAL and not _no_review(head_run)


def _no_review(run: ReviewRun) -> bool:
    """Ревью прогон не дал: упал, отменён или завершён без денег — для gate на паузе всё равно."""
    return (
        run.status in {ReviewRunStatus.FAILED, ReviewRunStatus.CANCELLED}
        or run.coverage_reason is CoverageReason.BALANCE
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


def build_in_progress_check() -> MergeGateCheck:
    """Прогон за head ещё не записал ревью (#295), в том числе после смены целевой ветки.

    Для обязательной проверки `in_progress` не пройден, как и `failure`: прежний `success`
    по старому диффу он перекрывает так же. Итог, `failure` или check паузы, его сменяет.
    """
    return MergeGateCheck(
        conclusion=None,
        title="Идёт ревью",
        summary="Ревью этого коммита ещё идёт. Мердж заблокирован до его итога.",
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


def build_unpaid_check(*, has_payer: bool) -> MergeGateCheck:
    """Прогон за head завершён без модели: у плательщика не хватило денег или его нет
    (`review-billing`). Ревью не было — мердж держится, пока ревью не пройдёт после пополнения."""
    fix = (
        "Пополните баланс плательщика репозитория или возьмите оплату на себя в кабинете"
        if has_payer
        else "У репозитория нет плательщика: назначьте его в кабинете («Платить за этот репозиторий»)"
    )
    return MergeGateCheck(
        conclusion="failure",
        title="Недостаточно средств, ревью не было",
        summary=f"{fix}, затем запустите ревью вручную или сделайте push. Мердж заблокирован до ревью.",
    )


def settle_merge_gate(
    uow: UnitOfWork,
    vcs: VcsGateway,
    *,
    merge_request_id: UUID,
    repo_full_name: str,
    head_sha: str,
    installation_id: int,
    repository_id: UUID,
    attempts: int = SETTLE_ATTEMPTS,
) -> None:
    """Выставить на head gate прогона, который за него отвечает (`head_run_in_charge`).

    Одно правило для всех, кто меняет состояние head: захват прогона, запись ревью,
    падение и очистка прогона, смена целевой ветки, закрытие треда (#295):

    - пауза ревью (`pause_check`) - check паузы: `failure` завершившегося прогона
      заблокировал бы PR, который ревью не ждёт;
    - прогон `completed` без денег (`coverage_reason = balance`) - `failure` «Недостаточно
      средств»;
    - прогон `completed` или записавший ревью - итог по открытым находкам и порогу;
    - прогон `failed` или `cancelled` - `failure` «Ревью не удалось»;
    - прогон ещё без ревью - `in_progress` «Идёт ревью».

    Записав ревью, прогон ещё `publishing`, но свой gate мог уже выставить и выйти:
    `in_progress`, дошедший после, остался бы на head (#330). Поэтому «ревью записано»
    смотрится по сводке прогона, а не по статусу.

    Гонки задержанных POST: после каждого POST состояние перечитывается, и если check
    изменился, выставляется снова. Приём смены ветки коммитит отмену и новый прогон до
    своего POST, поэтому любая перестановка кончается gate актуального прогона. Находки и
    порог читаются на каждой попытке: порог могли сменить, пока шёл прогон или сбой.
    После сбоя `attempts` попыток (по умолчанию `SETTLE_ATTEMPTS`) - `VcsError` вызывающему. Прогона на head нет -
    check не выставляется. Ручной прогон на паузе авторевью ставит свой gate (D11), после
    упавшего повторного ручного прогона gate - по находкам завершённого прежнего.
    """
    failures = 0
    # Последний выставленный check: пока GitHub его принимал, итог мог измениться из-за
    # любого входа - прогона, порога, тредов, паузы (#325). Сравниваем сам check.
    settled: tuple[MergeGateCheck, UUID | None] | None = None
    while True:
        # Перед каждой попыткой - заново: за время сбоя за head мог начать
        # отвечать другой прогон, и повтор прежнего check вернул бы чужой итог.
        with uow:
            repository = uow.repositories.get(repository_id)
            merge_request = uow.merge_requests.get(merge_request_id)
            latest = head_run_in_charge(uow, repository, merge_request_id, head_sha)
            published = latest is not None and any(
                c.kind is CommentKind.SUMMARY
                for c in uow.published_comments.list_for_run(latest.id)
            )
        if repository is None:
            raise LookupError(f"repository {repository_id} not found")
        if latest is None:
            return
        pause = pause_check(
            repository, latest, merge_request.target_branch if merge_request else None
        )
        if pause is not None:
            current = pause
        elif latest.coverage_reason is CoverageReason.BALANCE:
            current = build_unpaid_check(has_payer=repository.payer_user_id is not None)
        elif latest.status is ReviewRunStatus.COMPLETED or (
            latest.status is ReviewRunStatus.PUBLISHING and published
        ):
            current = build_merge_gate_check(
                collect_open_findings(uow, merge_request_id, head_run_id=latest.id),
                repository.min_severity,
            )
        elif latest.status in {ReviewRunStatus.FAILED, ReviewRunStatus.CANCELLED}:
            # Повторного ревью этим прогоном не будет: head ждёт push или ручной запуск.
            current = build_failed_review_check()
        else:
            current = build_in_progress_check()
        # Ссылка - на прогон за head; у check паузы её нет.
        link = None if pause is not None else latest.id
        if (current, link) == settled:
            return
        try:
            vcs.set_merge_gate_check(
                repo_full_name, head_sha, installation_id, current, run_id=link
            )
        except VcsError:
            failures += 1
            if failures >= attempts:
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
        settled = (current, link)
