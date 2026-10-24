"""Публикация ревью: комментарии, треды, merge gate."""

import itertools
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.ports.llm_gateway import LlmReviewResult, LlmThreadFollowUp
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
    ThreadFollowUpStatus,
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
DIFF_LIMIT = 5_242_880


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
        diff_max_bytes=DIFF_LIMIT,
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
            diff_max_bytes=DIFF_LIMIT,
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
            diff_max_bytes=DIFF_LIMIT,
        )


def test_thread_follow_up_fixed_resolves_on_host() -> None:
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
    vcs = RecordingVcsGateway()
    follow_up = LlmThreadFollowUp(
        anchor=DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=10),
        category=FindingCategory.CORRECTNESS,
        status=ThreadFollowUpStatus.FIXED,
        reply="теперь ок",
        resolve_thread=True,
    )
    result = LlmReviewResult(
        model="m",
        tokens_used=0,
        findings=(),
        thread_follow_ups=(follow_up,),
    )

    vcs.diff = (
        "diff --git a/app/x.py b/app/x.py\n"
        "--- a/app/x.py\n+++ b/app/x.py\n"
        "@@ -10,1 +10,1 @@\n"
        "-критично\n"
        "+исправлено\n"
    )
    publish_review(
        JOB, run, result, uow=uow, vcs=vcs, now=NOW, new_id=lambda: UUID(int=301), diff_max_bytes=DIFF_LIMIT
    )

    assert vcs.resolved == ["root-inline"]
    root = uow.published_comments.get(ROOT_COMMENT_ID)
    assert root is not None
    assert root.thread_resolved is True
    assert vcs.checks[-1].conclusion == "success"


def test_critical_fixed_not_resolved_when_incremental_diff_is_over_the_limit() -> None:
    """Инкрементальный дифф больше лимита не роняет публикацию: изменение строки
    якоря не подтверждено, тред критичного замечания остаётся открытым."""
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
    vcs = RecordingVcsGateway()
    # Этот же дифф в пределах лимита закрыл бы тред (`test_thread_follow_up_fixed_resolves_on_host`).
    vcs.diff = (
        "diff --git a/app/x.py b/app/x.py\n"
        "--- a/app/x.py\n+++ b/app/x.py\n"
        "@@ -10,1 +10,1 @@\n"
        "-критично\n"
        "+исправлено\n"
    )
    follow_up = LlmThreadFollowUp(
        anchor=DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=10),
        category=FindingCategory.CORRECTNESS,
        status=ThreadFollowUpStatus.FIXED,
        reply="теперь ок",
        resolve_thread=True,
    )
    result = LlmReviewResult(model="m", tokens_used=0, findings=(), thread_follow_ups=(follow_up,))

    publish_review(
        JOB, run, result, uow=uow, vcs=vcs, now=NOW, new_id=lambda: UUID(int=304), diff_max_bytes=10
    )

    assert vcs.resolved == []
    assert [body for _, body in vcs.replies] != []
    root = uow.published_comments.get(ROOT_COMMENT_ID)
    assert root is not None
    assert root.thread_resolved is False


def test_critical_fixed_not_resolved_while_anchor_still_in_diff() -> None:
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
    vcs = RecordingVcsGateway()
    vcs.diff = (
        "diff --git a/app/x.py b/app/x.py\n"
        "--- a/app/x.py\n+++ b/app/x.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-header\n"
        "+header changed\n"
    )
    follow_up = LlmThreadFollowUp(
        anchor=DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=10),
        category=FindingCategory.CORRECTNESS,
        status=ThreadFollowUpStatus.FIXED,
        reply="я думаю что ок",
        resolve_thread=True,
    )
    result = LlmReviewResult(
        model="m",
        tokens_used=0,
        findings=(),
        thread_follow_ups=(follow_up,),
    )

    publish_review(
        JOB, run, result, uow=uow, vcs=vcs, now=NOW, new_id=lambda: UUID(int=302), diff_max_bytes=DIFF_LIMIT
    )

    assert vcs.resolved == []
    root = uow.published_comments.get(ROOT_COMMENT_ID)
    assert root is not None
    assert root.thread_resolved is False


def test_critical_fixed_not_resolved_when_line_inserted_above_anchor() -> None:
    # Над строкой с замечанием добавили комментарий, сама строка не тронута:
    # новая строка 10 — добавленная, но старая 10 не изменена.
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
    vcs = RecordingVcsGateway()
    vcs.diff = (
        "diff --git a/app/x.py b/app/x.py\n"
        "--- a/app/x.py\n+++ b/app/x.py\n"
        "@@ -9,2 +9,3 @@\n"
        " line9\n"
        "+# исправлено, баг закрыт\n"
        " критично\n"
    )
    follow_up = LlmThreadFollowUp(
        anchor=DiffAnchor(file_path="app/x.py", side=DiffSide.NEW, new_line=10),
        category=FindingCategory.CORRECTNESS,
        status=ThreadFollowUpStatus.FIXED,
        reply="исправлено",
        resolve_thread=True,
    )
    result = LlmReviewResult(
        model="m",
        tokens_used=0,
        findings=(),
        thread_follow_ups=(follow_up,),
    )

    publish_review(
        JOB, run, result, uow=uow, vcs=vcs, now=NOW, new_id=lambda: UUID(int=303), diff_max_bytes=DIFF_LIMIT
    )

    assert vcs.resolved == []
    root = uow.published_comments.get(ROOT_COMMENT_ID)
    assert root is not None
    assert root.thread_resolved is False


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
        diff_max_bytes=DIFF_LIMIT,
    )

    ((_, (inline,)),) = vcs.publications
    assert inline.body.endswith(
        "Согласно пользовательскому правилу «Naming» (пункт 1: Имя функции - глагол): Имя не глагол."
    )


SECOND_FINDING_ID = UUID(int=70)
SECOND_ROOT_COMMENT_ID = UUID(int=71)
FIXED_LINE_10_DIFF = (
    "diff --git a/app/x.py b/app/x.py\n"
    "--- a/app/x.py\n+++ b/app/x.py\n"
    "@@ -10,1 +10,1 @@\n"
    "-критично\n"
    "+исправлено\n"
)


def _seed_second_thread(uow: FakeUnitOfWork) -> None:
    uow.findings.add(
        Finding(
            id=SECOND_FINDING_ID,
            review_run_id=UUID(int=4),
            anchor=DiffAnchor(file_path="app/z.py", side=DiffSide.NEW, new_line=5),
            category=FindingCategory.READABILITY,
            severity=FindingSeverity.LOW,
            message="имя",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    uow.published_comments.add(
        PublishedComment(
            id=SECOND_ROOT_COMMENT_ID,
            review_run_id=UUID(int=4),
            finding_id=SECOND_FINDING_ID,
            provider_comment_id="root-inline-2",
            kind=CommentKind.INLINE,
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )


def _follow_up(path: str, line: int, category: FindingCategory) -> LlmThreadFollowUp:
    return LlmThreadFollowUp(
        anchor=DiffAnchor(file_path=path, side=DiffSide.NEW, new_line=line),
        category=category,
        status=ThreadFollowUpStatus.FIXED,
        reply="исправлено",
        resolve_thread=True,
    )


FIRST_FOLLOW_UP = _follow_up("app/x.py", 10, FindingCategory.CORRECTNESS)
SECOND_FOLLOW_UP = _follow_up("app/z.py", 5, FindingCategory.READABILITY)


def _ids() -> Callable[[], UUID]:
    counter = itertools.count(400)
    return lambda: UUID(int=next(counter))


def _publish_follow_ups(
    uow: FakeUnitOfWork, vcs: RecordingVcsGateway, *follow_ups: LlmThreadFollowUp
) -> None:
    publish_review(
        JOB,
        _publishing_run(uow),
        LlmReviewResult(model="m", tokens_used=0, findings=(), thread_follow_ups=follow_ups),
        uow=uow,
        vcs=vcs,
        now=NOW,
        new_id=_ids(),
        diff_max_bytes=DIFF_LIMIT,
    )


def _replies_in_db(uow: FakeUnitOfWork) -> list[str | None]:
    return [
        c.in_reply_to_provider_comment_id
        for c in uow.published_comments.list_for_run(RUN_ID)
        if c.kind is CommentKind.THREAD_REPLY
    ]


def test_failed_reply_does_not_stop_the_next_follow_up_or_the_merge_gate(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """#76: ответ в первый тред упал - второй опубликован, gate выставлен."""
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    _seed_second_thread(uow)
    vcs = RecordingVcsGateway(
        diff=FIXED_LINE_10_DIFF,
        reply_errors={"root-inline": VcsTransientError("HTTP 502 на replies")},
    )

    with caplog.at_level(logging.WARNING, logger="app.application.publish_review"):
        _publish_follow_ups(uow, vcs, FIRST_FOLLOW_UP, SECOND_FOLLOW_UP)

    assert [root for root, _ in vcs.replies] == ["root-inline-2"]
    assert vcs.resolved == ["root-inline-2"]
    assert _replies_in_db(uow) == ["root-inline-2"]
    (check,) = vcs.checks
    assert check.conclusion == "failure"
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(RUN_ID) in record.getMessage()
    assert "root-inline" in record.getMessage()


def test_failed_resolve_keeps_the_reply_record_and_leaves_the_thread_open() -> None:
    """#76: закрытие треда упало после ответа - ответ в базе, тред открыт, gate выставлен."""
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    vcs = RecordingVcsGateway(
        diff=FIXED_LINE_10_DIFF,
        resolve_errors={"root-inline": VcsError("GitHub GraphQL: [{'message': 'boom'}]")},
    )

    _publish_follow_ups(uow, vcs, FIRST_FOLLOW_UP)

    assert _replies_in_db(uow) == ["root-inline"]
    root = uow.published_comments.get(ROOT_COMMENT_ID)
    assert root is not None
    assert root.thread_resolved is False
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_incremental_diff_failure_publishes_the_reply_without_resolving() -> None:
    """Дифф между коммитами не получен - как при превышении лимита: ответ есть, тред открыт."""
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    vcs = RecordingVcsGateway(diff_error=VcsTransientError("HTTP 502 на compare"))

    _publish_follow_ups(uow, vcs, FIRST_FOLLOW_UP)

    assert [root for root, _ in vcs.replies] == ["root-inline"]
    assert vcs.resolved == []
    assert _replies_in_db(uow) == ["root-inline"]
    (check,) = vcs.checks
    assert check.conclusion == "failure"


def test_programming_error_in_a_follow_up_is_not_swallowed() -> None:
    uow = FakeUnitOfWork()
    _seed_previous_critical_thread(uow)
    vcs = RecordingVcsGateway(reply_errors={"root-inline": AttributeError("опечатка")})

    with pytest.raises(AttributeError):
        _publish_follow_ups(uow, vcs, FIRST_FOLLOW_UP)
