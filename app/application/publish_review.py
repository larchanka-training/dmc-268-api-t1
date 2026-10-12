"""Публикация результатов прогона на GitHub: комментарии, треды, check run."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.application.ports.llm_gateway import (
    LlmReviewResult,
    LlmReviewSummary,
    LlmThreadFollowUp,
)
from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.vcs_review import (
    InlineCommentDraft,
    MergeGateCheck,
    PublishedInlineComment,
)
from app.domain.entities import (
    Finding,
    OpenReviewThread,
    PublishedComment,
    ReviewJob,
    ReviewRun,
)
from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    ThreadFollowUpStatus,
)
from app.domain.merge_gate import merge_blocked_by_findings
from app.domain.review_comment import (
    format_inline_finding_comment,
    format_summary_comment,
    format_thread_reply,
)


def _default_summary(findings: tuple[Finding, ...]) -> LlmReviewSummary:
    if not findings:
        return LlmReviewSummary(
            problem="Критичных проблем в диффе не обнаружено.",
            positives=(),
            effort="мерж без доработки по ревью бота",
        )
    return LlmReviewSummary(
        problem="См. инлайн-комментарии к изменённым строкам.",
        positives=(),
        effort="зависит от замечаний",
    )


def _line_for_anchor(finding: Finding) -> int:
    if finding.anchor.side is DiffSide.NEW:
        assert finding.anchor.new_line is not None
        return finding.anchor.new_line
    assert finding.anchor.old_line is not None
    return finding.anchor.old_line


def _match_published_to_finding(
    finding: Finding, published: tuple[PublishedInlineComment, ...]
) -> str | None:
    for item in published:
        if (
            item.file_path == finding.anchor.file_path
            and item.side == finding.anchor.side
            and item.line == _line_for_anchor(finding)
        ):
            return item.provider_comment_id
    return None


def _may_resolve_thread(status: ThreadFollowUpStatus, severity: FindingSeverity) -> bool:
    if status is ThreadFollowUpStatus.STILL_RELEVANT:
        return False
    if status is ThreadFollowUpStatus.ACCEPTED_RISK and severity in (
        FindingSeverity.CRITICAL,
        FindingSeverity.HIGH,
    ):
        return False
    return status in (
        ThreadFollowUpStatus.FIXED,
        ThreadFollowUpStatus.SUPERSEDED,
        ThreadFollowUpStatus.ACCEPTED_RISK,
    )


def publish_review(
    job: ReviewJob,
    run: ReviewRun,
    result: LlmReviewResult,
    *,
    uow: UnitOfWork,
    vcs: VcsGateway,
    now: datetime,
    new_id: Callable[[], UUID],
) -> None:
    """Опубликовать находки, ответы в треды и merge gate для одного прогона."""
    with uow:
        findings = tuple(uow.findings.list_for_run(run.id))
        open_threads = tuple(uow.published_comments.list_open_threads_for_merge_request(run.merge_request_id))
        merge_request = uow.merge_requests.get(run.merge_request_id)
    if merge_request is None:
        raise LookupError(f"merge request {run.merge_request_id} not found")

    summary = result.summary or _default_summary(findings)
    summary_body = format_summary_comment(summary.problem, summary.positives, summary.effort)

    inline_drafts = tuple(
        InlineCommentDraft(
            file_path=finding.anchor.file_path,
            side=finding.anchor.side,
            line=_line_for_anchor(finding),
            body=format_inline_finding_comment(finding),
        )
        for finding in findings
    )

    publication = vcs.publish_pull_request_review(
        job.repository_full_name,
        job.pull_request_number,
        job.head_sha,
        job.installation_id,
        summary_body,
        inline_drafts,
    )

    moment = now
    with uow:
        if publication.summary_provider_comment_id is not None:
            uow.published_comments.add(
                PublishedComment(
                    id=new_id(),
                    review_run_id=run.id,
                    finding_id=None,
                    provider_comment_id=publication.summary_provider_comment_id,
                    kind=CommentKind.SUMMARY,
                    published_at=moment,
                    created_at=moment,
                    updated_at=moment,
                )
            )
        for finding in findings:
            provider_id = _match_published_to_finding(finding, publication.inline_comments)
            if provider_id is None:
                continue
            uow.published_comments.add(
                PublishedComment(
                    id=new_id(),
                    review_run_id=run.id,
                    finding_id=finding.id,
                    provider_comment_id=provider_id,
                    kind=CommentKind.INLINE,
                    published_at=moment,
                    created_at=moment,
                    updated_at=moment,
                )
            )
        uow.commit()

    _publish_thread_follow_ups(
        job,
        run,
        result.thread_follow_ups,
        open_threads=open_threads,
        uow=uow,
        vcs=vcs,
        now=now,
        new_id=new_id,
    )

    still_open = _collect_open_findings(uow, run.merge_request_id)
    blocked = merge_blocked_by_findings(still_open)
    conclusion = "failure" if blocked else "success"
    title = "Есть критичные замечания" if blocked else "Ревью пройдено"
    gate_summary = (
        "Остаются незакрытые замечания уровня critical — мердж заблокирован."
        if blocked
        else "Критичных незакрытых замечаний нет."
    )
    vcs.set_merge_gate_check(
        job.repository_full_name,
        job.head_sha,
        job.installation_id,
        MergeGateCheck(conclusion=conclusion, title=title, summary=gate_summary),
    )


def _collect_open_findings(uow: UnitOfWork, merge_request_id: UUID) -> tuple[Finding, ...]:
    with uow:
        threads = uow.published_comments.list_open_threads_for_merge_request(merge_request_id)
    return tuple(t.finding for t in threads)


def _publish_thread_follow_ups(
    job: ReviewJob,
    run: ReviewRun,
    follow_ups: tuple[LlmThreadFollowUp, ...],
    *,
    open_threads: tuple[OpenReviewThread, ...],
    uow: UnitOfWork,
    vcs: VcsGateway,
    now: datetime,
    new_id: Callable[[], UUID],
) -> None:
    if not follow_ups:
        return
    by_key: dict[tuple[str, DiffSide, int, FindingCategory], OpenReviewThread] = {}
    for thread in open_threads:
        anchor_line = _line_for_anchor(thread.finding)
        key = (
            thread.finding.anchor.file_path,
            thread.finding.anchor.side,
            anchor_line,
            thread.finding.category,
        )
        by_key[key] = thread

    moment = now
    for item in follow_ups:
        follow_up_line = (
            item.anchor.new_line if item.anchor.side is DiffSide.NEW else item.anchor.old_line
        )
        if follow_up_line is None:
            continue
        thread = by_key.get(
            (item.anchor.file_path, item.anchor.side, follow_up_line, item.category)
        )
        if thread is None:
            continue
        body = format_thread_reply(item.status, item.reply, severity=thread.finding.severity)
        reply_id = vcs.reply_to_review_comment(
            job.repository_full_name,
            job.installation_id,
            thread.root_provider_comment_id,
            body,
        )
        resolve = item.resolve_thread and _may_resolve_thread(item.status, thread.finding.severity)
        if resolve:
            vcs.resolve_review_thread_for_comment(
                job.repository_full_name,
                job.pull_request_number,
                job.installation_id,
                thread.root_provider_comment_id,
            )
        with uow:
            uow.published_comments.add(
                PublishedComment(
                    id=new_id(),
                    review_run_id=run.id,
                    finding_id=thread.finding.id,
                    provider_comment_id=reply_id,
                    kind=CommentKind.THREAD_REPLY,
                    published_at=moment,
                    created_at=moment,
                    updated_at=moment,
                    in_reply_to_provider_comment_id=thread.root_provider_comment_id,
                )
            )
            if resolve:
                root = uow.published_comments.get(thread.root_published_comment_id)
                if root is not None:
                    uow.published_comments.update(
                        replace(root, thread_resolved=True, updated_at=moment)
                    )
            uow.commit()
