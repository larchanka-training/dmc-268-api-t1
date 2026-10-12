"""Публикация ревью: комментарии, треды, merge gate."""

from datetime import UTC, datetime
from uuid import UUID

from app.application.ports.llm_gateway import LlmReviewResult, LlmThreadFollowUp
from app.application.publish_review import publish_review
from app.domain.entities import (
    DiffAnchor,
    Finding,
    MergeRequest,
    PublishedComment,
    ReviewJob,
    ReviewRun,
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

    publish_review(JOB, run, result, uow=uow, vcs=vcs, now=NOW, new_id=lambda: UUID(int=301))

    assert vcs.resolved == ["root-inline"]
    root = uow.published_comments.get(ROOT_COMMENT_ID)
    assert root is not None
    assert root.thread_resolved is True
    assert vcs.checks[-1].conclusion == "success"
