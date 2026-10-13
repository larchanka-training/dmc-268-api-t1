"""Check run merge gate по открытым критичным тредам."""

from datetime import datetime
from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
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


def refresh_merge_gate_for_merge_request(
    merge_request_id: UUID,
    *,
    repo_full_name: str,
    pr_number: int,
    head_sha: str,
    installation_id: int,
    uow: UnitOfWork,
    vcs: VcsGateway,
    now: datetime,
) -> None:
    from app.application.sync_thread_resolution import sync_open_threads_from_host

    sync_open_threads_from_host(
        merge_request_id,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        installation_id=installation_id,
        uow=uow,
        vcs=vcs,
        now=now,
    )
    still_open = collect_open_findings(uow, merge_request_id)
    vcs.set_merge_gate_check(
        repo_full_name,
        head_sha,
        installation_id,
        build_merge_gate_check(still_open),
    )
