"""Выметание зависших прогонов — фейковый `UnitOfWork`, без БД."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.stale_sweep import sweep_stale_runs
from app.domain.entities import ReviewRun
from app.domain.enums import (
    ReviewRunStage,
    ReviewRunStatus,
    ReviewRunStepStatus,
    TriggerSource,
)
from app.domain.run_steps import finish_step, start_step

from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
LIMIT = timedelta(minutes=30)
QUEUED_LIMIT = timedelta(hours=6)


def _run(n: int, status: ReviewRunStatus, idle: timedelta) -> ReviewRun:
    moment = NOW - idle
    return ReviewRun(
        id=UUID(int=n),
        merge_request_id=UUID(int=99),
        head_sha=f"sha{n}",
        status=status,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=moment,
        created_at=moment,
        updated_at=moment,
    )


def test_a_run_without_progress_beyond_the_limit_is_failed() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.BUILDING_CONTEXT, timedelta(hours=1)))

    swept = sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT)

    run = uow.review_runs.get(UUID(int=1))
    assert swept == 1
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
    assert run.failure_reason
    assert run.last_progress_at == NOW


def test_a_swept_run_keeps_the_stage_it_stalled_in() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.PUBLISHING, timedelta(hours=1)))

    sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT)

    run = uow.review_runs.get(UUID(int=1))
    assert run is not None
    assert run.failed_stage is ReviewRunStatus.PUBLISHING


def test_a_run_within_the_limit_and_a_terminal_run_are_left_alone() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(minutes=5)))
    uow.review_runs.add(_run(2, ReviewRunStatus.COMPLETED, timedelta(hours=5)))

    swept = sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT)

    assert swept == 0
    assert uow.review_runs.get(UUID(int=1)).status == ReviewRunStatus.ANALYSING  # type: ignore[union-attr]
    assert uow.review_runs.get(UUID(int=2)).status == ReviewRunStatus.COMPLETED  # type: ignore[union-attr]


def test_a_swept_run_closes_the_step_its_worker_left_running() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    started = NOW - timedelta(hours=1)
    done = finish_step(
        start_step(UUID(int=1), ReviewRunStage.BUILDING_CONTEXT, started_at=started, step_id=UUID(int=10)),
        duration_seconds=2.0,
        tokens_used=None,
    )
    uow.review_run_steps.add(done)
    uow.review_run_steps.add(
        start_step(UUID(int=1), ReviewRunStage.ANALYSING, started_at=started, step_id=UUID(int=11))
    )

    sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT)

    kept, closed = uow.review_run_steps.list_for_run(UUID(int=1))
    assert kept == done
    assert closed.status is ReviewRunStepStatus.FAILED
    assert closed.error_code == "REVIEW_RUN_STALE"
    assert closed.duration_seconds == 3600.0


def test_a_failure_closing_the_steps_does_not_undo_the_sweep() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    uow.review_run_steps.add(
        start_step(UUID(int=1), ReviewRunStage.ANALYSING, started_at=NOW, step_id=UUID(int=11))
    )

    def broken(step: object) -> None:
        raise ConnectionError("база недоступна")

    uow.review_run_steps.update = broken  # type: ignore[method-assign]

    assert sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT) == 1
    assert uow.review_runs.get(UUID(int=1)).status == ReviewRunStatus.FAILED  # type: ignore[union-attr]


def test_a_queued_run_is_failed_only_after_the_queued_limit() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.QUEUED, timedelta(hours=1)))
    uow.review_runs.add(_run(2, ReviewRunStatus.QUEUED, timedelta(hours=7)))

    swept = sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT)

    assert swept == 1
    waiting = uow.review_runs.get(UUID(int=1))
    lost = uow.review_runs.get(UUID(int=2))
    assert waiting is not None and waiting.status == ReviewRunStatus.QUEUED
    assert lost is not None and lost.status == ReviewRunStatus.FAILED
    assert lost.failure_reason == "нет прогресса дольше 6:00:00"
