"""Публикация ревью: комментарии, треды, merge gate."""

import itertools
import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.ports.llm_gateway import LlmReviewResult
from app.application.ports.vcs_review import InlineCommentDraft, ReviewThreadState
from app.application.publish_review import publish_review
from app.domain.entities import (
    DiffAnchor,
    Finding,
    MergeRequest,
    PublishedComment,
    ReviewJob,
    ReviewRun,
    RuleRef,
)
from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.vcs_errors import VcsError, VcsTransientError

from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.vcs_gateway import RecordingVcsGateway

NOW = datetime(2026, 10, 12, tzinfo=UTC)
MR_ID = UUID(int=3)
RUN_ID = UUID(int=2)
FINDING_ID = UUID(int=50)
ROOT_COMMENT_ID = UUID(int=51)


JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=RUN_ID,
    event_type="pull_request",
    action="synchronize",
    installation_id=1,
    repository_provider_id="1",
    repository_full_name="owner/repo",
    pull_request_number=7,
    head_sha="sha2",
    base_sha="base",
)


def _seed_previous_critical_thread(uow: FakeUnitOfWork) -> None:
    uow.merge_requests.add(
        MergeRequest(
            id=MR_ID,
            repository_id=UUID(int=9),
            number=7,
            title="t",
            description="",
            author="a",
            source_branch="feat",
            target_branch="develop",
            head_sha="sha1",
            state=MergeRequestState.OPEN,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    prev_run = ReviewRun(
        id=UUID(int=4),
        merge_request_id=MR_ID,
        head_sha="sha1",
        status=ReviewRunStatus.COMPLETED,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.review_runs.add(prev_run)
    finding = Finding(
        id=FINDING_ID,
        review_run_id=prev_run.id,
        anchor=DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=10),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.CRITICAL,
        message="критично",
        created_at=NOW,
        updated_at=NOW,
    )
    uow.findings.add(finding)
    uow.published_comments.add(
        PublishedComment(
            id=ROOT_COMMENT_ID,
            review_run_id=prev_run.id,
            finding_id=FINDING_ID,
            provider_comment_id="root-inline",
            kind=CommentKind.INLINE,
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def test_merge_gate_fails_while_critical_thread_open() -> None:
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    uow.review_runs.add(
        ReviewRun(
            id=RUN_ID,
            merge_request_id=MR_ID,
            head_sha=JOB.head_sha,
            status=ReviewRunStatus.PUBLISHING,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    vcs = RecordingVcsGateway()
    result = LlmReviewResult(model="m", tokens_used=0, findings=())

    publish_review(
        JOB,
        uow.review_runs.get(RUN_ID),  # type: ignore[arg-type]
        result,
        uow=uow,
        vcs=vcs,
        now=NOW,
        new_id=lambda: UUID(int=300),
    )

    assert vcs.checks[-1].conclusion == "failure"


def _publishing_run(uow: FakeUnitOfWork) -> ReviewRun:
    run = ReviewRun(
        id=RUN_ID,
        merge_request_id=MR_ID,
        head_sha=JOB.head_sha,
        status=ReviewRunStatus.PUBLISHING,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.review_runs.add(run)
    return run


@pytest.mark.parametrize(
    "error",
    [
        VcsTransientError("GitHub API отвечал 429/5xx 3 попытки подряд на POST /graphql"),
        VcsError("GitHub GraphQL: [{'message': 'Something went wrong'}]"),
    ],
    ids=["unavailable", "graphql-error"],
)
def test_thread_sync_failure_still_sets_the_merge_gate_from_the_database(
    error: VcsError, caplog: pytest.LogCaptureFixture
) -> None:
    """#76: сверка тредов упала после публикации - прогон не падает, gate по базе."""
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    run = _publishing_run(uow)
    vcs = RecordingVcsGateway(threads_error=error)

    with caplog.at_level(logging.WARNING, logger="app.application.publish_review"):
        publish_review(
            JOB,
            run,
            LlmReviewResult(model="m", tokens_used=0, findings=()),
            uow=uow,
            vcs=vcs,
            now=NOW,
            new_id=lambda: UUID(int=302),
        )

    (check,) = vcs.checks
    assert check.conclusion == "failure"
    assert len(vcs.publications) == 1
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(RUN_ID) in record.getMessage()
    assert record.exc_info is not None and record.exc_info[1] is error


def test_thread_sync_programming_error_is_not_swallowed() -> None:
    """Не сбой хостинга, а ошибка в коде - прогон падает, как раньше."""
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    run = _publishing_run(uow)
    vcs = RecordingVcsGateway(threads_error=AttributeError("опечатка"))

    with pytest.raises(AttributeError):
        publish_review(
            JOB,
            run,
            LlmReviewResult(model="m", tokens_used=0, findings=()),
            uow=uow,
            vcs=vcs,
            now=NOW,
            new_id=lambda: UUID(int=303),
        )


def test_inline_comment_is_attributed_from_the_stored_rule_reference() -> None:
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    run = ReviewRun(
        id=RUN_ID,
        merge_request_id=MR_ID,
        head_sha=JOB.head_sha,
        status=ReviewRunStatus.PUBLISHING,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.review_runs.add(run)
    uow.findings.add(
        Finding(
            id=UUID(int=60),
            review_run_id=RUN_ID,
            anchor=DiffAnchor(file_path="app/y.py", side=DiffSide.NEW, new_line=3),
            category=FindingCategory.READABILITY,
            severity=FindingSeverity.LOW,
            message="Имя не глагол.",
            created_at=NOW,
            updated_at=NOW,
            rule=RuleRef("Naming", 1, "Имя функции - глагол"),
        )
    )
    vcs = RecordingVcsGateway()

    publish_review(
        JOB,
        run,
        LlmReviewResult(model="m", tokens_used=0, findings=()),
        uow=uow,
        vcs=vcs,
        now=NOW,
        new_id=lambda: UUID(int=301),
    )

    ((_, (inline,)),) = vcs.publications
    assert inline.body.endswith(
        "Согласно пользовательскому правилу «Naming» (пункт 1: Имя функции - глагол): Имя не глагол."
    )


def _ids() -> Callable[[], UUID]:
    counter = itertools.count(400)
    return lambda: UUID(int=next(counter))


# --- #148: повторный прогон не дублирует замечание с тредом бота ---------------

NEW_FINDING_ID = UUID(int=80)


def _seed_prior_thread(
    uow: FakeUnitOfWork,
    *,
    severity: FindingSeverity,
    resolved: bool = False,
    resolved_by_bot: bool = False,
) -> None:
    """Тред бота с прошлого прогона на `app/x.py:10` (correctness) нужной серьёзности."""
    _seed_previous_critical_thread(uow)
    (finding,) = uow.findings.list_for_run(UUID(int=4))
    uow.findings.restore(
        [replace(f, severity=severity) if f.id == finding.id else f for f in uow.findings.snapshot()]
    )
    root = uow.published_comments.get(ROOT_COMMENT_ID)
    assert root is not None
    uow.published_comments.update(
        replace(root, thread_resolved=resolved, thread_resolved_by_bot=resolved_by_bot)
    )


def _current_finding(
    uow: FakeUnitOfWork, *, severity: FindingSeverity, line: int = 10
) -> None:
    uow.findings.add(
        Finding(
            id=NEW_FINDING_ID,
            review_run_id=RUN_ID,
            anchor=DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=line),
            category=FindingCategory.CORRECTNESS,
            severity=severity,
            message="та же проблема",
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _on_host(*, line: int | None, resolved: bool = False) -> RecordingVcsGateway:
    return RecordingVcsGateway(
        thread_states={"root-inline": ReviewThreadState(resolved=resolved, line=line)}
    )


def _publish(uow: FakeUnitOfWork, vcs: RecordingVcsGateway) -> None:
    publish_review(
        JOB,
        _publishing_run(uow),
        LlmReviewResult(model="m", tokens_used=0, findings=()),
        uow=uow,
        vcs=vcs,
        now=NOW,
        new_id=_ids(),
    )


def _inline_drafts(vcs: RecordingVcsGateway) -> tuple[InlineCommentDraft, ...]:
    ((_, drafts),) = vcs.publications
    return drafts


def _stored_new_finding(uow: FakeUnitOfWork) -> Finding:
    (finding,) = [f for f in uow.findings.list_for_run(RUN_ID) if f.id == NEW_FINDING_ID]
    return finding


def test_rerun_with_the_same_finding_publishes_no_new_inline_comment() -> None:
    """#148: открытый тред на той же строке - замечание не повторяется, находка связана с тредом."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()
    assert _stored_new_finding(uow).covered_by_finding_id == FINDING_ID
    inline = [c for c in uow.published_comments.list_for_run(RUN_ID) if c.kind is CommentKind.INLINE]
    assert inline == []
    # Подавленная находка gate не ломает: критичный тред открыт - мердж заблокирован.
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_lower_severity_finding_is_covered_by_the_open_thread() -> None:
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.HIGH)
    _current_finding(uow, severity=FindingSeverity.MEDIUM)
    vcs = _on_host(line=10)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()
    assert _stored_new_finding(uow).covered_by_finding_id == FINDING_ID


def test_finding_that_became_critical_is_published_next_to_the_open_thread() -> None:
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.HIGH)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10)

    _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert (draft.file_path, draft.line) == ("app/x.py", 10)
    assert _stored_new_finding(uow).covered_by_finding_id is None
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_outdated_thread_does_not_suppress_the_finding() -> None:
    """GitHub отдаёт `line: null` у устаревшего треда: к нынешней строке он не привязан."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=None)

    _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert (draft.file_path, draft.line) == ("app/x.py", 10)


def test_thread_is_matched_by_its_current_line_on_the_host() -> None:
    """Над строкой добавили две строки: тред с 10-й переехал на 12-ю, туда же и находка."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL, line=12)
    vcs = _on_host(line=12)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()
    assert _stored_new_finding(uow).covered_by_finding_id == FINDING_ID


def test_stored_line_alone_does_not_match_a_moved_thread() -> None:
    """Тред уехал на 12-ю: находка на 10-й - другое место, публикуется."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL, line=10)
    vcs = _on_host(line=12)

    _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert draft.line == 10


def test_thread_resolved_by_a_human_suppresses_the_finding() -> None:
    """#169, п. 3а: тред закрыл человек - замечание повторно не публикуется."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL, resolved=True)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10, resolved=True)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()
    assert _stored_new_finding(uow).covered_by_finding_id == FINDING_ID
    (check,) = vcs.checks
    assert check.conclusion == "success"


def test_thread_resolved_on_the_host_without_a_webhook_counts_as_resolved_by_a_human() -> None:
    """В базе тред открыт, на GitHub его закрыл человек: сверка до публикации это видит."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.HIGH)
    _current_finding(uow, severity=FindingSeverity.HIGH)
    vcs = _on_host(line=10, resolved=True)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()
    root = uow.published_comments.get(ROOT_COMMENT_ID)
    assert root is not None
    assert (root.thread_resolved, root.thread_resolved_by_bot) == (True, False)


def test_critical_finding_escalated_past_a_human_resolved_thread_is_published() -> None:
    """#169, п. 3а: исключение - критичное замечание, ставшее серьёзнее, публикуется в любом случае."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.HIGH, resolved=True)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10, resolved=True)

    _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert draft.line == 10
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_non_critical_escalation_past_a_human_resolved_thread_is_not_published() -> None:
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.LOW, resolved=True)
    _current_finding(uow, severity=FindingSeverity.HIGH)
    vcs = _on_host(line=10, resolved=True)

    _publish(uow, vcs)

    assert _inline_drafts(vcs) == ()


def test_finding_returns_after_the_bot_resolved_its_thread_as_fixed() -> None:
    """Тред закрыл сам бот как исправленный, а проблема вернулась - замечание публикуется снова."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(
        uow, severity=FindingSeverity.CRITICAL, resolved=True, resolved_by_bot=True
    )
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10, resolved=True)

    _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert draft.line == 10
    assert _stored_new_finding(uow).covered_by_finding_id is None
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_host_thread_state_is_fetched_once_per_run() -> None:
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = _on_host(line=10)

    _publish(uow, vcs)

    assert vcs.thread_state_requests == [frozenset({"root-inline"})]


def test_without_host_thread_state_every_finding_is_published(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Сверка не удалась: текущая строка треда неизвестна - лучше повтор, чем потерянное замечание."""
    uow = FakeUnitOfWork()
    _seed_prior_thread(uow, severity=FindingSeverity.CRITICAL)
    _current_finding(uow, severity=FindingSeverity.CRITICAL)
    vcs = RecordingVcsGateway(threads_error=VcsTransientError("HTTP 502 на /graphql"))

    with caplog.at_level(logging.WARNING, logger="app.application.publish_review"):
        _publish(uow, vcs)

    (draft,) = _inline_drafts(vcs)
    assert draft.line == 10
    (check,) = vcs.checks
    assert check.conclusion == "failure"


CRITICAL_FINDING_ID = UUID(int=90)


def _run_with_critical_finding(uow: FakeUnitOfWork) -> ReviewRun:
    """Первый прогон PR: базы тредов нет, у прогона одна находка `critical`."""
    _seed_merge_request(uow)
    run = _publishing_run(uow)
    uow.findings.add(
        Finding(
            id=CRITICAL_FINDING_ID,
            review_run_id=RUN_ID,
            anchor=DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=10),
            category=FindingCategory.SECURITY,
            severity=FindingSeverity.CRITICAL,
            message="SQL-инъекция",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return run


def _seed_merge_request(uow: FakeUnitOfWork) -> None:
    uow.merge_requests.add(
        MergeRequest(
            id=MR_ID,
            repository_id=UUID(int=9),
            number=7,
            title="t",
            description="",
            author="a",
            source_branch="feat",
            target_branch="develop",
            head_sha=JOB.head_sha,
            state=MergeRequestState.OPEN,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _publish_first_run(uow: FakeUnitOfWork, run: ReviewRun, vcs: RecordingVcsGateway) -> None:
    publish_review(
        JOB,
        run,
        LlmReviewResult(model="m", tokens_used=0, findings=()),
        uow=uow,
        vcs=vcs,
        now=NOW,
        new_id=_ids(),
    )


def test_the_review_carries_the_marker_of_its_run() -> None:
    """По маркеру адаптер находит своё ревью, если ответ на POST потерялся (#185)."""
    uow = FakeUnitOfWork()
    vcs = RecordingVcsGateway()

    _publish_first_run(uow, _run_with_critical_finding(uow), vcs)

    assert vcs.review_run_ids == [RUN_ID]


def test_lost_inline_ids_keep_the_summary_and_block_the_merge() -> None:
    """#185: id inline не получены - сводка записана, gate видит `critical` без треда в базе."""
    uow = FakeUnitOfWork()
    vcs = RecordingVcsGateway(inline_ids_lost=True)

    _publish_first_run(uow, _run_with_critical_finding(uow), vcs)

    assert [(c.kind, c.provider_comment_id) for c in uow.published_comments.list_for_run(RUN_ID)] == [
        (CommentKind.SUMMARY, "summary-1")
    ]
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_merge_gate_failure_after_publishing_does_not_fail_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#185: check run не выставился - ревью уже на GitHub, повтор дал бы второе; warning в лог."""
    uow = FakeUnitOfWork()
    error = VcsTransientError("GitHub API отвечал 429/5xx 3 попытки подряд на POST /check-runs")
    vcs = RecordingVcsGateway(check_error=error)

    with caplog.at_level(logging.WARNING, logger="app.application.publish_review"):
        _publish_first_run(uow, _run_with_critical_finding(uow), vcs)

    assert len(vcs.publications) == 1
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(RUN_ID) in record.getMessage()
    assert record.exc_info is not None and record.exc_info[1] is error
