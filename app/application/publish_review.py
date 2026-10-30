"""Публикация результатов прогона на GitHub: комментарии, сверка тредов, check run."""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.application.merge_gate_publish import (
    build_merge_gate_check,
    collect_open_findings,
    settle_merge_gate,
)
from app.application.ports.llm_gateway import LlmReviewResult, LlmReviewSummary
from app.application.ports.unit_of_work import ActiveRunConflict, UnitOfWork
from app.application.ports.vcs_gateway import VcsGateway
from app.application.ports.vcs_review import (
    InlineCommentDraft,
    PullRequestReviewPublication,
)
from app.application.sync_thread_resolution import record_threads_resolved_on_host
from app.domain.entities import (
    BotThread,
    Finding,
    PublishedComment,
    ReviewJob,
    ReviewRun,
)
from app.domain.enums import CommentKind, ReviewRunStatus
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
    _adopt_orphaned_reviews(job, run, uow=uow, vcs=vcs, now=now, new_id=new_id)
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

    inline_drafts = _inline_drafts(to_publish)

    with uow:
        current = uow.review_runs.get(run.id)
    if current is not None and current.status is ReviewRunStatus.CANCELLED:
        # Целевую ветку сменили, пока прогон работал (#222): его ревью и gate
        # описывали бы дифф против старой ветки, а замечания стали бы тредами в gate
        # нового прогона. Head заблокировал приём, ревью и gate выставит новый прогон.
        # Отмену после этой проверки поправит `settle_merge_gate` после POST gate.
        logger.info("прогон %s отменён до публикации: ревью не публикуется", run.id)
        return

    publication = vcs.publish_pull_request_review(
        job.repository_full_name,
        job.pull_request_number,
        job.head_sha,
        job.installation_id,
        summary_body,
        inline_drafts,
        review_run_id=run.id,
    )

    with uow:
        _record_publication(uow, run.id, to_publish, publication, now=now, new_id=new_id)
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
        # Ветку могли сменить после проверки статуса до публикации (#222).
        settle_merge_gate(
            uow,
            vcs,
            merge_request_id=run.merge_request_id,
            repo_full_name=job.repository_full_name,
            head_sha=job.head_sha,
            installation_id=job.installation_id,
            run_id=run.id,
        )
    except VcsError:
        logger.warning(
            "прогон %s: ревью опубликовано, а check run merge gate на %s не выставлен",
            run.id,
            job.head_sha,
            exc_info=True,
        )


def _inline_drafts(findings: tuple[Finding, ...]) -> tuple[InlineCommentDraft, ...]:
    return tuple(
        InlineCommentDraft(
            file_path=finding.anchor.file_path,
            side=finding.anchor.side,
            line=_line_for_anchor(finding),
            body=format_inline_finding_comment(finding),
        )
        for finding in findings
    )


def _record_publication(
    uow: UnitOfWork,
    run_id: UUID,
    findings: tuple[Finding, ...],
    publication: PullRequestReviewPublication,
    *,
    now: datetime,
    new_id: Callable[[], UUID],
) -> None:
    """Записать ревью прогона в `published_comments` (без коммита): сводку и inline
    с id, сопоставленные с находками в порядке черновиков."""
    if publication.summary_provider_comment_id is not None:
        uow.published_comments.add(
            PublishedComment(
                id=new_id(),
                review_run_id=run_id,
                finding_id=None,
                provider_comment_id=publication.summary_provider_comment_id,
                kind=CommentKind.SUMMARY,
                published_at=now,
                created_at=now,
                updated_at=now,
            )
        )
    for finding, published_item in zip(findings, publication.inline_comments, strict=True):
        if published_item is None:
            continue
        uow.published_comments.add(
            PublishedComment(
                id=new_id(),
                review_run_id=run_id,
                finding_id=finding.id,
                provider_comment_id=published_item.provider_comment_id,
                kind=CommentKind.INLINE,
                published_at=now,
                created_at=now,
                updated_at=now,
            )
        )


def _adopt_orphaned_reviews(
    job: ReviewJob,
    run: ReviewRun,
    *,
    uow: UnitOfWork,
    vcs: VcsGateway,
    now: datetime,
    new_id: Callable[[], UUID],
) -> None:
    """Записать ревью прогонов PR, упавших на публикации без следа в базе (#222).

    Воркер умер между `POST …/reviews` и записью `published_comments`: ревью с
    маркером прогона есть на GitHub, а отсев по треду его не видит и повторил бы
    замечание. Найденное ревью записывается упавшему прогону, его inline
    сопоставляются с его находками. Best-effort: сбой хостинга - warning, без
    подбора публикация идёт как раньше. Ошибки кода (не `VcsError`) не гасятся.
    """
    with uow:
        orphans = [
            (orphan, tuple(uow.findings.list_for_run(orphan.id)))
            for orphan in uow.review_runs.list_unpublished_failures(run.merge_request_id)
        ]
    for orphan, findings in orphans:
        drafts = _inline_drafts(findings)
        try:
            publication = vcs.find_published_review(
                job.repository_full_name,
                job.pull_request_number,
                job.installation_id,
                drafts,
                review_run_id=orphan.id,
            )
        except VcsError:
            logger.warning(
                "прогон %s: ревью упавшего прогона %s на GitHub не проверено",
                run.id,
                orphan.id,
                exc_info=True,
            )
            continue
        if publication is None:
            continue
        with uow:
            _record_publication(uow, orphan.id, findings, publication, now=now, new_id=new_id)
            try:
                uow.commit()
            except ActiveRunConflict:
                # Соседний прогон PR подобрал это ревью раньше: записи уже есть.
                uow.rollback()
                logger.info(
                    "прогон %s: ревью упавшего прогона %s уже записал другой прогон",
                    run.id,
                    orphan.id,
                )
                continue
        logger.warning(
            "прогон %s: ревью упавшего прогона %s найдено по маркеру и записано",
            run.id,
            orphan.id,
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
