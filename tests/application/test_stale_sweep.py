"""Выметание зависших прогонов — фейковый `UnitOfWork`, без БД."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.stale_sweep import sweep_stale_runs
from app.domain.entities import ReviewRun
from app.domain.enums import ReviewRunStatus, TriggerSource

from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
LIMIT = timedelta(minutes=30)


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

    swept = sweep_stale_runs(uow, now=NOW, limit=LIMIT)

    run = uow.review_runs.get(UUID(int=1))
    assert swept == 1
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
    assert run.failure_reason
    assert run.last_progress_at == NOW


def test_a_run_within_the_limit_and_a_terminal_run_are_left_alone() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(minutes=5)))
    uow.review_runs.add(_run(2, ReviewRunStatus.COMPLETED, timedelta(hours=5)))

    swept = sweep_stale_runs(uow, now=NOW, limit=LIMIT)

    assert swept == 0
    assert uow.review_runs.get(UUID(int=1)).status == ReviewRunStatus.ANALYSING  # type: ignore[union-attr]
    assert uow.review_runs.get(UUID(int=2)).status == ReviewRunStatus.COMPLETED  # type: ignore[union-attr]
