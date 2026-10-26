"""Публикация результатов прогона на GitHub: комментарии, сверка тредов, check run."""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.application.merge_gate_publish import (
    build_merge_gate_check,
    collect_open_findings,
)
from app.application.ports.llm_gateway import LlmReviewResult, LlmReviewSummary
from app.application.ports.unit_of_work import UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.vcs_review import InlineCommentDraft
from app.application.sync_thread_resolution import record_threads_resolved_on_host
from app.domain.entities import (
    BotThread,
    Finding,
    PublishedComment,
    ReviewJob,
    ReviewRun,
)
from app.domain.enums import CommentKind
from app.domain.review_comment import (
    format_inline_finding_comment,
    format_summary_comment,
)
from app.domain.thread_coverage import thread_coverage
from app.domain.vcs_errors import VcsError

logger = logging.getLogger(__name__)


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
    # Сохранённое замечание прошло `validate_anchor`: строка на его стороне есть.
    line = finding.anchor.line
    assert line is not None
    return line


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
    """Опубликовать находки и merge gate для одного прогона.

    Находка, о которой уже говорит тред бота с прошлого прогона, своего
    комментария не получает (`thread_coverage`), а связывается с находкой
    треда. `threadFollowUps` из ответа модели не публикуются и тредов не
    закрывают: модель читает дифф, который пишет автор PR, и её «исправлено»
    снимало бы блокировку мерджа по инъекции (#73). Тред закрывает человек.
    """
    with uow:
        findings = tuple(uow.findings.list_for_run(run.id))
        threads = tuple(uow.published_comments.list_threads_for_merge_request(run.merge_request_id))
        merge_request = uow.merge_requests.get(run.merge_request_id)
    if merge_request is None:
        raise LookupError(f"merge request {run.merge_request_id} not found")

    threads, current_lines = _sync_threads_with_host(job, run, threads, uow=uow, vcs=vcs, now=now)
    covered: dict[UUID, BotThread] = {}
    for finding in findings:
        coverage = thread_coverage(finding, threads, current_lines)
        if coverage.covered_by is not None:
            covered[finding.id] = coverage.covered_by
    to_publish = tuple(f for f in findings if f.id not in covered)

    summary = result.summary or _default_summary(findings)
    summary_body = format_summary_comment(summary.problem, summary.positives, summary.effort)

    inline_drafts = tuple(
        InlineCommentDraft(
            file_path=finding.anchor.file_path,
            side=finding.anchor.side,
            line=_line_for_anchor(finding),
            body=format_inline_finding_comment(finding),
        )
        for finding in to_publish
    )

    publication = vcs.publish_pull_request_review(
        job.repository_full_name,
        job.pull_request_number,
        job.head_sha,
        job.installation_id,
        summary_body,
        inline_drafts,
        review_run_id=run.id,
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
            to_publish, publication.inline_comments, strict=True
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
        for finding_id, thread in covered.items():
            uow.findings.mark_covered(finding_id, thread.finding.id)
        uow.commit()

    # Треды сверены с GitHub до публикации: gate - по базе, с находками этого
    # прогона, чей id inline не получен.
    still_open = collect_open_findings(uow, run.merge_request_id, head_run_id=run.id)
    # Best-effort: ревью уже в PR, а `failed` после публикации ведёт к новому
    # прогону и второму ревью. Без check run обязательная проверка не даёт мерджа
    # до следующего прогона или вебхука закрытия треда.
    try:
        vcs.set_merge_gate_check(
            job.repository_full_name,
            job.head_sha,
            job.installation_id,
            build_merge_gate_check(still_open),
        )
    except VcsError:
        logger.warning(
            "прогон %s: ревью опубликовано, а check run merge gate на %s не выставлен",
            run.id,
            job.head_sha,
            exc_info=True,
        )


def _sync_threads_with_host(
    job: ReviewJob,
    run: ReviewRun,
    threads: tuple[BotThread, ...],
    *,
    uow: UnitOfWork,
    vcs: VcsGateway,
    now: datetime,
) -> tuple[tuple[BotThread, ...], dict[str, int | None]]:
    """Сверить треды бота с GitHub одним запросом: закрытие и текущая строка.

    Тред, закрытый на хостинге не ботом, записывается закрытым человеком.
    Сверка - best-effort: сбой хостинга пишется в лог и не роняет прогон
    (повтор после `failed` опубликовал бы второе ревью). Без ответа хостинга
    строки тредов неизвестны, ни одна находка не считается покрытой, а gate
    считается по базе. Ошибки кода (не `VcsError`) не гасятся.
    """
    # Тред, закрытый ботом, ничего не покрывает: его состояние не нужно.
    asked = tuple(t for t in threads if not t.resolved_by_bot)
    if not asked:
        return threads, {}
    try:
        states = vcs.fetch_review_threads_state(
            job.repository_full_name,
            job.pull_request_number,
            job.installation_id,
            frozenset(t.root_provider_comment_id for t in asked),
        )
    except VcsError:
        logger.warning(
            "прогон %s: сверка тредов %s#%s с GitHub не удалась, merge gate - по базе",
            run.id,
            job.repository_full_name,
            job.pull_request_number,
            exc_info=True,
        )
        return threads, {}
    marked = record_threads_resolved_on_host(
        ((t.root_provider_comment_id, t.root_published_comment_id) for t in asked if not t.resolved),
        states,
        uow=uow,
        now=now,
    )
    synced = tuple(
        replace(t, resolved=True, resolved_by_bot=False)
        if t.root_provider_comment_id in marked
        else t
        for t in threads
    )
    return synced, {root: state.line for root, state in states.items()}
