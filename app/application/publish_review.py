"""Публикация результатов прогона на GitHub: комментарии, треды, check run."""

from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.application.merge_gate_publish import (
    build_merge_gate_check,
    collect_open_findings,
)
from app.application.ports.llm_gateway import (
    LlmReviewResult,
    LlmReviewSummary,
    LlmThreadFollowUp,
)
from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.vcs_review import InlineCommentDraft
from app.application.sync_thread_resolution import sync_open_threads_from_host
from app.domain.diff_parser import parse_diff
from app.domain.entities import (
    DiffAnchor,
    Finding,
    Hunk,
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
from app.domain.review_comment import (
    format_inline_finding_comment,
    format_summary_comment,
    format_thread_reply,
)
from app.domain.vcs_errors import VcsFileTooLargeError


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


def _hunks_between_commits(
    vcs: VcsGateway,
    job: ReviewJob,
    base_sha: str,
    head_sha: str,
    *,
    max_bytes: int,
) -> tuple[Hunk, ...]:
    """Hunk'и между прошлым и текущим head; дифф больше лимита - пусто.

    Пустой результат значит «изменение строки якоря не подтверждено»: тред
    критичного замечания не закрывается автоматически, а уже готовое ревью
    из-за лимита не падает.
    """
    if base_sha == head_sha:
        return ()
    try:
        diff_text = vcs.fetch_diff(
            job.repository_full_name, base_sha, head_sha, job.installation_id, max_bytes=max_bytes
        )
    except VcsFileTooLargeError:
        return ()
    return tuple(hunk for parsed in parse_diff(diff_text) for hunk in parsed.hunks)


def _may_resolve_thread(
    status: ThreadFollowUpStatus,
    severity: FindingSeverity,
    anchor: DiffAnchor,
    incremental_hunks: tuple[Hunk, ...],
) -> bool:
    if status is ThreadFollowUpStatus.STILL_RELEVANT:
        return False
    if status not in (ThreadFollowUpStatus.FIXED, ThreadFollowUpStatus.SUPERSEDED):
        return False
    if status is ThreadFollowUpStatus.FIXED and severity is FindingSeverity.CRITICAL:
        return _anchor_line_changed(anchor, incremental_hunks)
    return True


def _anchor_line_changed(anchor: DiffAnchor, incremental_hunks: tuple[Hunk, ...]) -> bool:
    """Изменена ли сама строка якоря между прошлым и текущим head.

    Номер строки якоря — в координатах прошлого head, то есть старой стороны
    диффа `prev_head..head`. Якорь на удалённой строке (`OLD`) в координатах
    base, этим диффом не подтверждается — такой тред закрывает человек.
    """
    if anchor.side is not DiffSide.NEW or anchor.new_line is None:
        return False
    return any(
        hunk.file_path == anchor.file_path and anchor.new_line in hunk.changed_old_lines
        for hunk in incremental_hunks
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
    diff_max_bytes: int,
) -> None:
    """Опубликовать находки, ответы в треды и merge gate для одного прогона.

    `diff_max_bytes` ограничивает инкрементальный дифф, которым подтверждается
    изменение строки якоря критичного замечания.
    """
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
        for finding, published_item in zip(
            findings, publication.inline_comments, strict=True
        ):
            if published_item is None:
                continue
            provider_id = published_item.provider_comment_id
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
        diff_max_bytes=diff_max_bytes,
    )

    sync_open_threads_from_host(
        run.merge_request_id,
        repo_full_name=job.repository_full_name,
        pr_number=job.pull_request_number,
        installation_id=job.installation_id,
        uow=uow,
        vcs=vcs,
        now=now,
    )

    still_open = collect_open_findings(uow, run.merge_request_id)
    vcs.set_merge_gate_check(
        job.repository_full_name,
        job.head_sha,
        job.installation_id,
        build_merge_gate_check(still_open),
    )


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
    diff_max_bytes: int,
) -> None:
    if not follow_ups:
        return
    incremental_hunks_cache: dict[str, tuple[Hunk, ...]] = {}
    by_key: dict[tuple[str, DiffSide, int, FindingCategory], OpenReviewThread] = {}
    for open_thread in open_threads:
        anchor_line = _line_for_anchor(open_thread.finding)
        key = (
            open_thread.finding.anchor.file_path,
            open_thread.finding.anchor.side,
            anchor_line,
            open_thread.finding.category,
        )
        by_key[key] = open_thread

    moment = now
    for item in follow_ups:
        follow_up_line = (
            item.anchor.new_line if item.anchor.side is DiffSide.NEW else item.anchor.old_line
        )
        if follow_up_line is None:
            continue
        matched = by_key.get(
            (item.anchor.file_path, item.anchor.side, follow_up_line, item.category)
        )
        if matched is None:
            continue
        with uow:
            prev_run = uow.review_runs.get(matched.finding.review_run_id)
        prev_head = prev_run.head_sha if prev_run is not None else job.head_sha
        if prev_head not in incremental_hunks_cache:
            incremental_hunks_cache[prev_head] = _hunks_between_commits(
                vcs, job, prev_head, job.head_sha, max_bytes=diff_max_bytes
            )
        incremental_hunks = incremental_hunks_cache[prev_head]
        body = format_thread_reply(item.status, item.reply, severity=matched.finding.severity)
        reply_id = vcs.reply_to_review_comment(
            job.repository_full_name,
            job.pull_request_number,
            job.installation_id,
            matched.root_provider_comment_id,
            body,
        )
        resolve = item.resolve_thread and _may_resolve_thread(
            item.status,
            matched.finding.severity,
            matched.finding.anchor,
            incremental_hunks,
        )
        if resolve:
            vcs.resolve_review_thread_for_comment(
                job.repository_full_name,
                job.pull_request_number,
                job.installation_id,
                matched.root_provider_comment_id,
            )
        with uow:
            uow.published_comments.add(
                PublishedComment(
                    id=new_id(),
                    review_run_id=run.id,
                    finding_id=matched.finding.id,
                    provider_comment_id=reply_id,
                    kind=CommentKind.THREAD_REPLY,
                    published_at=moment,
                    created_at=moment,
                    updated_at=moment,
                    in_reply_to_provider_comment_id=matched.root_provider_comment_id,
                )
            )
            if resolve:
                root = uow.published_comments.get(matched.root_published_comment_id)
                if root is not None:
                    uow.published_comments.update(
                        replace(root, thread_resolved=True, updated_at=moment)
                    )
            uow.commit()
