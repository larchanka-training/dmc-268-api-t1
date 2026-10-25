"""Публикация результатов прогона на GitHub: комментарии, треды, check run."""

import logging
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
from app.application.sync_thread_resolution import record_threads_resolved_on_host
from app.domain.diff_parser import parse_diff
from app.domain.entities import (
    BotThread,
    DiffAnchor,
    Finding,
    Hunk,
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
from app.domain.thread_coverage import thread_coverage
from app.domain.vcs_errors import VcsError, VcsFileTooLargeError

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

    Находка, о которой уже говорит тред бота с прошлого прогона, своего
    комментария не получает (`thread_coverage`), а связывается с находкой
    треда. `diff_max_bytes` ограничивает инкрементальный дифф, которым
    подтверждается изменение строки якоря критичного замечания.
    """
    with uow:
        findings = tuple(uow.findings.list_for_run(run.id))
        threads = tuple(uow.published_comments.list_threads_for_merge_request(run.merge_request_id))
        merge_request = uow.merge_requests.get(run.merge_request_id)
    if merge_request is None:
        raise LookupError(f"merge request {run.merge_request_id} not found")

    threads, current_lines = _sync_threads_with_host(job, run, threads, uow=uow, vcs=vcs, now=now)
    covered: dict[UUID, BotThread] = {}
    superseded: set[str] = set()
    for finding in findings:
        coverage = thread_coverage(finding, threads, current_lines)
        if coverage.covered_by is not None:
            covered[finding.id] = coverage.covered_by
        superseded.update(t.root_provider_comment_id for t in coverage.superseded)
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

    _publish_thread_follow_ups(
        job,
        run,
        result.thread_follow_ups,
        open_threads=tuple(t for t in threads if not t.resolved),
        # Ответ в тред, вместо которого опубликован новый inline, повторил бы
        # замечание дважды; тред, который покрывает находку этого прогона,
        # не закрывается: проблема на месте.
        skip_roots=frozenset(superseded),
        keep_open_roots=frozenset(
            t.root_provider_comment_id for t in covered.values() if not t.resolved
        ),
        uow=uow,
        vcs=vcs,
        now=now,
        new_id=new_id,
        diff_max_bytes=diff_max_bytes,
    )

    # Треды сверены с GitHub до публикации, свои закрытия бот записал сам:
    # gate - по базе, с находками этого прогона, чей id inline не получен.
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


def _publish_thread_follow_ups(
    job: ReviewJob,
    run: ReviewRun,
    follow_ups: tuple[LlmThreadFollowUp, ...],
    *,
    open_threads: tuple[BotThread, ...],
    skip_roots: frozenset[str],
    keep_open_roots: frozenset[str],
    uow: UnitOfWork,
    vcs: VcsGateway,
    now: datetime,
    new_id: Callable[[], UUID],
    diff_max_bytes: int,
) -> None:
    if not follow_ups:
        return
    incremental_hunks_cache: dict[str, tuple[Hunk, ...]] = {}
    by_key: dict[tuple[str, DiffSide, int, FindingCategory], BotThread] = {}
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
    # Модель может вернуть два follow-up на одно место: второй ответ в тот же тред
    # упал бы на `uq_published_comments_finding` уже после ответа на GitHub.
    answered: set[str] = set()
    for item in follow_ups:
        follow_up_line = item.anchor.line
        if follow_up_line is None:
            continue
        matched = by_key.get(
            (item.anchor.file_path, item.anchor.side, follow_up_line, item.category)
        )
        if matched is None:
            continue
        root_id = matched.root_provider_comment_id
        if root_id in answered or root_id in skip_roots:
            continue
        with uow:
            prev_run = uow.review_runs.get(matched.finding.review_run_id)
        prev_head = prev_run.head_sha if prev_run is not None else job.head_sha
        if prev_head not in incremental_hunks_cache:
            try:
                incremental_hunks_cache[prev_head] = _hunks_between_commits(
                    vcs, job, prev_head, job.head_sha, max_bytes=diff_max_bytes
                )
            except VcsError:
                # Как при превышении лимита: изменение строки якоря не подтверждено.
                logger.warning(
                    "прогон %s: дифф %s..%s для треда %s не получен, критичный тред не закрывается",
                    run.id,
                    prev_head,
                    job.head_sha,
                    root_id,
                    exc_info=True,
                )
                incremental_hunks_cache[prev_head] = ()
        incremental_hunks = incremental_hunks_cache[prev_head]
        body = format_thread_reply(item.status, item.reply, severity=matched.finding.severity)
        # Ответы - после публикации ревью: сбой хостинга на одном треде пишется в
        # лог и не мешает следующим, сверке и gate.
        try:
            reply_id = vcs.reply_to_review_comment(
                job.repository_full_name,
                job.pull_request_number,
                job.installation_id,
                root_id,
                body,
            )
        except VcsError:
            logger.warning(
                "прогон %s: ответ в тред %s не опубликован", run.id, root_id, exc_info=True
            )
            continue
        answered.add(root_id)
        # Ответ записывается до закрытия треда: сбой закрытия не теряет запись,
        # и следующий прогон не ответит повторно.
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
                    in_reply_to_provider_comment_id=root_id,
                )
            )
            uow.commit()
        resolve = root_id not in keep_open_roots and item.resolve_thread and _may_resolve_thread(
            item.status,
            matched.finding.severity,
            matched.finding.anchor,
            incremental_hunks,
        )
        if not resolve:
            continue
        try:
            vcs.resolve_review_thread_for_comment(
                job.repository_full_name,
                job.pull_request_number,
                job.installation_id,
                root_id,
            )
        except VcsError:
            logger.warning(
                "прогон %s: тред %s не закрыт на GitHub после ответа", run.id, root_id, exc_info=True
            )
            continue
        with uow:
            root = uow.published_comments.get(matched.root_published_comment_id)
            if root is not None:
                uow.published_comments.update(
                    replace(root, thread_resolved=True, thread_resolved_by_bot=True, updated_at=moment)
                )
            uow.commit()
