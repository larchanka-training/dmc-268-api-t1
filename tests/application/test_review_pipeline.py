"""Оркестрация одного прогона ревью — фейковый `UnitOfWork`, без БД и брокера."""

from collections.abc import Callable
from datetime import UTC, datetime
from itertools import count
from uuid import UUID

from app.application.ports.llm_gateway import LlmFinding, LlmReviewResult
from app.application.review_pipeline import run_review
from app.domain.entities import DiffAnchor, ReviewJob, ReviewRun
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    ReviewRunStatus,
    TriggerSource,
)

from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

JOB = ReviewJob(
    id=UUID(int=1),
    event_type="pull_request",
    action="opened",
    repository_provider_id="987654",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="abc123def",
    base_sha="fed654cba",
)


def _seed_run(uow: FakeUnitOfWork, *, status: ReviewRunStatus = ReviewRunStatus.QUEUED) -> None:
    uow.review_runs.add(
        ReviewRun(
            id=JOB.id,
            merge_request_id=UUID(int=2),
            head_sha=JOB.head_sha,
            status=status,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
    )


class StubGateway:
    def __init__(self, result: LlmReviewResult) -> None:
        self._result = result

    def review(self, context: object) -> LlmReviewResult:
        return self._result


class FailingGateway:
    def review(self, context: object) -> LlmReviewResult:
        raise RuntimeError("модель недоступна")


def _new_id() -> Callable[[], UUID]:
    counter = count(100)
    return lambda: UUID(int=next(counter))


def test_successful_run_reaches_completed_with_model_and_tokens() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = StubGateway(LlmReviewResult(model="stub-llm", tokens_used=0, findings=()))

    run_review(JOB, uow=uow, llm_gateway=gateway, now=NOW, new_id=_new_id())

    run = uow.review_runs.get(JOB.id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.model == "stub-llm"
    assert run.tokens_used == 0
    assert run.duration_seconds is not None
    assert run.duration_seconds >= 0
    assert uow.context_payloads.list_for_run(JOB.id)


def test_a_finding_anchored_in_the_stub_hunk_is_persisted() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    finding = LlmFinding(
        anchor=DiffAnchor(file_path="PLACEHOLDER.md", side=DiffSide.NEW, new_line=1),
        category=FindingCategory.READABILITY,
        severity=FindingSeverity.LOW,
        message="заготовочная находка",
    )
    gateway = StubGateway(LlmReviewResult(model="stub-llm", tokens_used=5, findings=(finding,)))

    run_review(JOB, uow=uow, llm_gateway=gateway, now=NOW, new_id=_new_id())

    findings = uow.findings.list_for_run(JOB.id)
    assert len(findings) == 1
    assert findings[0].message == "заготовочная находка"


def test_llm_failure_marks_the_run_failed_with_a_reason() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    run_review(JOB, uow=uow, llm_gateway=FailingGateway(), now=NOW, new_id=_new_id())

    run = uow.review_runs.get(JOB.id)
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
    assert run.failure_reason == "модель недоступна"


def test_missing_run_is_a_silent_no_op() -> None:
    uow = FakeUnitOfWork()
    gateway = StubGateway(LlmReviewResult(model="m", tokens_used=0, findings=()))

    run_review(JOB, uow=uow, llm_gateway=gateway, now=NOW, new_id=_new_id())

    assert uow.review_runs.get(JOB.id) is None


def test_already_terminal_run_is_left_untouched() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow, status=ReviewRunStatus.COMPLETED)
    gateway = StubGateway(LlmReviewResult(model="m", tokens_used=0, findings=()))

    run_review(JOB, uow=uow, llm_gateway=gateway, now=NOW, new_id=_new_id())

    run = uow.review_runs.get(JOB.id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.model is None
