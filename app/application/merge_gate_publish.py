"""Check run merge gate по открытым критичным тредам."""

from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_review import MergeGateCheck
from app.domain.entities import Finding
from app.domain.merge_gate import merge_blocked_by_findings, untracked_findings


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
