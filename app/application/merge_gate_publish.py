"""Check run merge gate по открытым критичным тредам."""

from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_review import MergeGateCheck
from app.domain.entities import Finding
from app.domain.merge_gate import merge_blocked_by_findings


def collect_open_findings(uow: UnitOfWork, merge_request_id: UUID) -> tuple[Finding, ...]:
    with uow:
        threads = uow.published_comments.list_open_threads_for_merge_request(merge_request_id)
    return tuple(t.finding for t in threads)


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
