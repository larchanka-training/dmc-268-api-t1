"""Check run merge gate по открытым критичным тредам."""

import logging
from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.vcs_review import MergeGateCheck
from app.domain.entities import Finding
from app.domain.enums import ReviewRunStatus
from app.domain.merge_gate import merge_blocked_by_findings, untracked_findings
from app.domain.vcs_errors import VcsError

logger = logging.getLogger(__name__)

# Поправку gate отправляет только тот, кто увидел смену прогона, и второй
# возможности у неё нет: после сбоя на head остаётся `success` по старому диффу.
# Адаптер сам повторяет 5xx и лимит; здесь - ещё попытки после его отказа.
SETTLE_ATTEMPTS = 3


def collect_open_findings(
    uow: UnitOfWork, merge_request_id: UUID, *, head_run_id: UUID | None
) -> tuple[Finding, ...]:
    """Открытые треды PR и находки прогона на head, чей id inline не получен.

    `head_run_id` - прогон, чьё ревью стоит на текущем head; `None` - такого нет.
    """
    with uow:
        threads = uow.published_comments.list_open_threads_for_merge_request(merge_request_id)
        untracked: tuple[Finding, ...] = ()
        if head_run_id is not None:
            untracked = untracked_findings(
                uow.findings.list_for_run(head_run_id),
                uow.published_comments.list_for_run(head_run_id),
            )
    return tuple(t.finding for t in threads) + untracked


def build_merge_gate_check(open_findings: tuple[Finding, ...]) -> MergeGateCheck:
    blocked = merge_blocked_by_findings(open_findings)
    conclusion = "failure" if blocked else "success"
    title = "Есть критичные замечания" if blocked else "Ревью пройдено"
    summary = (
        "Остаются незакрытые замечания уровня critical — мердж заблокирован."
        if blocked
        else "Критичных незакрытых замечаний нет."
    )
    return MergeGateCheck(conclusion=conclusion, title=title, summary=summary)


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
) -> None:
    """Поправить gate, только что выставленный по прогону `run_id`, если тот уже не
    отвечает за head (#222).

    Гонка со сменой целевой ветки: приём сначала коммитит отмену старого прогона и
    новый прогон, потом выставляет блокировку. Кто выставил gate по старому прогону,
    перечитывает последний прогон на head после своего POST: если приём уже
    закоммитил, здесь видна смена и gate переписывается; если нет - блокировка
    приёма придёт позже и перекроет `success`. Так любая перестановка кончается
    gate актуального прогона. Поправка повторяется `SETTLE_ATTEMPTS` раз; сбой
    последней попытки - `VcsError` вызывающему.
    """
    with uow:
        latest = uow.review_runs.find_latest(merge_request_id, head_sha)
    if latest is None or (latest.id == run_id and latest.status is not ReviewRunStatus.CANCELLED):
        return
    if latest.status is ReviewRunStatus.COMPLETED:
        # Новый прогон успел завершиться: его gate мог уйти раньше нашего POST.
        check = build_merge_gate_check(
            collect_open_findings(uow, merge_request_id, head_run_id=latest.id)
        )
    else:
        check = build_retarget_check()
    logger.info(
        "merge gate %s@%s переписан: прогон %s больше не отвечает за head, последний - %s",
        repo_full_name,
        head_sha,
        run_id,
        latest.id,
    )
    for attempt in range(1, SETTLE_ATTEMPTS + 1):
        try:
            vcs.set_merge_gate_check(repo_full_name, head_sha, installation_id, check)
            return
        except VcsError:
            if attempt == SETTLE_ATTEMPTS:
                raise
            logger.warning(
                "поправка merge gate %s@%s не выставлена, попытка %d из %d",
                repo_full_name,
                head_sha,
                attempt,
                SETTLE_ATTEMPTS,
                exc_info=True,
            )
