"""Оркестрация одного прогона ревью — фейковый `UnitOfWork`, без БД и брокера."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import UUID

import pytest

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
    review_run_id=UUID(int=2),
    event_type="pull_request",
    action="opened",
    installation_id=512804923,
    repository_provider_id="987654",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="abc123def",
    base_sha="fed654cba",
)


def _seed_run(uow: FakeUnitOfWork, *, status: ReviewRunStatus = ReviewRunStatus.QUEUED) -> None:
    uow.review_runs.add(
        ReviewRun(
            id=JOB.review_run_id,
            merge_request_id=UUID(int=3),
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


class _Clock:
    """Часы, идущие на секунду при каждом чтении: переходы различимы по времени."""

    def __init__(self) -> None:
        self.reads = 0

    def now(self) -> datetime:
        self.reads += 1
        return NOW + timedelta(seconds=self.reads)


class _Monotonic:
    def __init__(self, *values: float) -> None:
        self._values = iter(values)

    def __call__(self) -> float:
        return next(self._values)


def _run(uow: FakeUnitOfWork, gateway: object, **overrides: object) -> None:
    kwargs: dict[str, object] = {
        "uow": uow,
        "llm_gateway": gateway,
        "now": _Clock().now,
        "monotonic": _Monotonic(0.0, 12.5),
        "new_id": _new_id(),
    }
    kwargs.update(overrides)
    run_review(JOB, **kwargs)  # type: ignore[arg-type]


def _new_id() -> Callable[[], UUID]:
    counter = count(100)
    return lambda: UUID(int=next(counter))


def test_successful_run_reaches_completed_with_model_and_tokens() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    gateway = StubGateway(LlmReviewResult(model="stub-llm", tokens_used=0, findings=()))

    _run(uow, gateway)

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.model == "stub-llm"
    assert run.tokens_used == 0
    assert run.duration_seconds == 12.5
    assert uow.context_payloads.list_for_run(JOB.review_run_id)


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

    _run(uow, gateway)

    findings = uow.findings.list_for_run(JOB.review_run_id)
    assert len(findings) == 1
    assert findings[0].message == "заготовочная находка"


def test_llm_failure_marks_the_run_failed_with_a_reason_and_reraises() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    with pytest.raises(RuntimeError, match="модель недоступна"):
        _run(uow, FailingGateway())

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
    assert run.failure_reason == "модель недоступна"


def test_missing_run_raises_so_the_message_goes_to_the_dlq() -> None:
    uow = FakeUnitOfWork()
    gateway = StubGateway(LlmReviewResult(model="m", tokens_used=0, findings=()))

    with pytest.raises(LookupError):
        _run(uow, gateway)

    assert uow.review_runs.get(JOB.review_run_id) is None


def test_last_progress_at_follows_the_clock_on_every_transition() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    seen: list[datetime] = []
    original = uow.review_runs.update

    def recording_update(run: ReviewRun) -> None:
        seen.append(run.last_progress_at)
        original(run)

    uow.review_runs.update = recording_update  # type: ignore[method-assign]
    gateway = StubGateway(LlmReviewResult(model="m", tokens_used=0, findings=()))

    _run(uow, gateway)

    assert len(seen) == 4  # building_context, analysing, publishing, completed
    assert seen == sorted(seen)
    assert len(set(seen)) == 4


def test_a_rejected_anchor_keeps_the_other_findings_and_the_count() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    def finding(path: str, message: str) -> LlmFinding:
        return LlmFinding(
            anchor=DiffAnchor(file_path=path, side=DiffSide.NEW, new_line=1),
            category=FindingCategory.READABILITY,
            severity=FindingSeverity.LOW,
            message=message,
        )

    gateway = StubGateway(
        LlmReviewResult(
            model="m",
            tokens_used=1,
            findings=(
                finding("PLACEHOLDER.md", "внутри диффа"),
                finding("вне/диффа.py", "вне диффа"),
                finding("PLACEHOLDER.md", "тоже внутри"),
            ),
        )
    )

    _run(uow, gateway)

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.rejected_findings == 1
    assert {f.message for f in uow.findings.list_for_run(JOB.review_run_id)} == {
        "внутри диффа",
        "тоже внутри",
    }


def test_a_failure_while_marking_failed_does_not_mask_the_original_error() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    def broken_update(run: ReviewRun) -> None:
        if run.status == ReviewRunStatus.FAILED:
            raise ConnectionError("база недоступна")

    real_update = uow.review_runs.update

    def update(run: ReviewRun) -> None:
        broken_update(run)
        real_update(run)

    uow.review_runs.update = update  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="модель недоступна"):
        _run(uow, FailingGateway())


def test_marking_failed_reads_the_fresh_row_not_the_stale_copy() -> None:
    """Выметатель уже перевёл строку в failed — вторая запись не падает и не затирает причину."""
    uow = FakeUnitOfWork()
    _seed_run(uow)

    class SweptGateway:
        def review(self, context: object) -> LlmReviewResult:
            current = uow.review_runs.get(JOB.review_run_id)
            assert current is not None
            swept = replace(current, status=ReviewRunStatus.FAILED, failure_reason="выметен")
            uow.review_runs.update(swept)
            raise RuntimeError("сбой после выметания")

    with pytest.raises(RuntimeError, match="сбой после выметания"):
        _run(uow, SweptGateway())

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
    assert run.failure_reason == "выметен"


def test_already_terminal_run_is_left_untouched() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow, status=ReviewRunStatus.COMPLETED)
    gateway = StubGateway(LlmReviewResult(model="m", tokens_used=0, findings=()))

    _run(uow, gateway)

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.model is None
