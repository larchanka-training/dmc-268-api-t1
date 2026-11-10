"""Агрегаты GET /analytics на настоящей базе: группировка по UTC-дню, длительности, видимость, период."""

import datetime as dt

import pytest
from sqlalchemy import create_engine, event

from app.domain.analytics import (
    EMPTY_ANALYTICS,
    AnalyticsFilter,
    FindingsDayCount,
)
from app.domain.entities import DiffAnchor, Finding, MergeRequest, Repository, ReviewRun
from app.domain.enums import (
    CoverageReason,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.ids import new_id
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import TEST_DATABASE_URL, requires_db

pytestmark = [pytest.mark.integration, requires_db]

FROM = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
TO = dt.datetime(2026, 11, 1, tzinfo=dt.UTC)
SEC, READ = FindingCategory.SECURITY, FindingCategory.READABILITY
LOW, HIGH = FindingSeverity.LOW, FindingSeverity.HIGH


def _repository(name: str) -> Repository:
    return Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id=name,
        full_name=f"acme/{name}",
        default_branch="main",
        auto_review_enabled=True,
        created_at=FROM,
        updated_at=FROM,
        connected_at=FROM,
    )


def _merge_request(repository: Repository) -> MergeRequest:
    return MergeRequest(
        id=new_id(),
        repository_id=repository.id,
        number=1,
        title="t",
        description="",
        author="a",
        source_branch="feat",
        target_branch="main",
        head_sha="abc",
        state=MergeRequestState.OPEN,
        created_at=FROM,
        updated_at=FROM,
    )


class World:
    def __init__(self) -> None:
        self.rows: list[Repository | MergeRequest | ReviewRun | Finding] = []
        self.api, self.web = _repository("api"), _repository("web")
        self.mrs = {}
        for repository in (self.api, self.web):
            mr = _merge_request(repository)
            self.mrs[repository.id] = mr
            self.rows += [repository, mr]

    def run(
        self,
        repository: Repository,
        at: dt.datetime,
        duration: float | None,
        *findings: tuple[FindingCategory, FindingSeverity],
        status: ReviewRunStatus = ReviewRunStatus.COMPLETED,
        coverage_reason: CoverageReason | None = None,
    ) -> None:
        run = ReviewRun(
            id=new_id(),
            merge_request_id=self.mrs[repository.id].id,
            head_sha=f"sha{len(self.rows)}",
            status=status,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=at,
            created_at=at,
            updated_at=at,
            duration_seconds=duration,
            coverage_reason=coverage_reason,
        )
        self.rows.append(run)
        for line, (category, severity) in enumerate(findings, start=1):
            self.rows.append(
                Finding(
                    id=new_id(),
                    review_run_id=run.id,
                    anchor=DiffAnchor(file_path="app.py", side=DiffSide.NEW, new_line=line),
                    category=category,
                    severity=severity,
                    message="m",
                    created_at=at,
                    updated_at=at,
                )
            )

    def store(self, uow: SqlAlchemyUnitOfWork) -> None:
        for kind, repo in (
            (Repository, "repositories"),
            (MergeRequest, "merge_requests"),
            (ReviewRun, "review_runs"),
            (Finding, "findings"),
        ):
            with uow:
                for row in self.rows:
                    if isinstance(row, kind):
                        getattr(uow, repo).add(row)
                uow.commit()


@pytest.fixture
def uow(clean_db):
    return SqlAlchemyUnitOfWork(clean_db)


def _analytics(uow, *repositories: Repository, start: dt.datetime = FROM, end: dt.datetime = TO):
    with uow:
        return uow.review_runs.analytics(AnalyticsFilter(frozenset(r.id for r in repositories), start, end))


def test_aggregates_by_utc_day_category_severity_and_repository(uow) -> None:
    world = World()
    day3 = dt.datetime(2026, 10, 3, 20, 30, tzinfo=dt.UTC)
    for seconds in (10.0, 20.0, 30.0, 40.0):
        world.run(world.api, day3, seconds, (SEC, HIGH))
    world.run(world.api, dt.datetime(2026, 10, 4, tzinfo=dt.UTC), 100.0, (SEC, LOW), (READ, LOW))
    world.run(world.web, day3, 50.0)
    world.store(uow)

    result = _analytics(uow, world.api, world.web)

    assert result.findings_by_day == (
        FindingsDayCount(dt.date(2026, 10, 3), SEC, HIGH, 4),
        FindingsDayCount(dt.date(2026, 10, 4), SEC, LOW, 1),
        FindingsDayCount(dt.date(2026, 10, 4), READ, LOW, 1),
    )
    assert result.runs.count == 6
    assert result.runs.average_duration_seconds == pytest.approx(250 / 6)
    assert result.runs.p95_duration_seconds == pytest.approx(87.5)
    api, web = result.by_repository
    assert (api.repository_id, api.full_name, api.runs_count, api.findings_count) == (world.api.id, "acme/api", 5, 6)
    assert api.average_duration_seconds == pytest.approx(40.0)
    assert api.p95_duration_seconds == pytest.approx(88.0)
    assert (web.repository_id, web.runs_count, web.findings_count) == (world.web.id, 1, 0)
    assert web.p95_duration_seconds == pytest.approx(50.0)


def test_day_is_utc_whatever_the_session_time_zone(clean_db) -> None:
    """В Токио 20:30Z 3 октября — уже 4 октября; группировка от зоны сессии не зависит."""
    world = World()
    world.run(world.api, dt.datetime(2026, 10, 3, 20, 30, tzinfo=dt.UTC), 1.0, (SEC, HIGH))
    world.store(SqlAlchemyUnitOfWork(clean_db))
    assert TEST_DATABASE_URL is not None
    tokyo = create_engine(TEST_DATABASE_URL, connect_args={"options": "-c timezone=Asia/Tokyo"})
    try:
        result = _analytics(SqlAlchemyUnitOfWork(tokyo), world.api)
    finally:
        tokyo.dispose()

    assert [d.date for d in result.findings_by_day] == [dt.date(2026, 10, 3)]


def test_only_visible_completed_runs_that_did_review_in_the_period(uow) -> None:
    world = World()
    inside = FROM + dt.timedelta(days=1)
    world.run(world.api, inside, 10.0, (SEC, LOW))
    world.run(world.api, inside, 0.1, coverage_reason=CoverageReason.BALANCE)
    world.run(world.api, inside, 500.0, (SEC, HIGH), status=ReviewRunStatus.FAILED)
    world.run(world.api, inside, None, status=ReviewRunStatus.ANALYSING)
    world.run(world.api, FROM - dt.timedelta(microseconds=1), 1.0, (SEC, HIGH))
    world.run(world.api, FROM, 2.0)
    world.run(world.api, TO, 4.0, (SEC, HIGH))
    world.run(world.web, inside, 99.0, (READ, HIGH))
    world.store(uow)

    result = _analytics(uow, world.api)

    assert result.runs.count == 2
    assert (result.runs.average_duration_seconds, result.runs.p95_duration_seconds) == (pytest.approx(6.0), pytest.approx(9.6))
    assert result.findings_by_day == (FindingsDayCount(inside.date(), SEC, LOW, 1),)
    assert [r.repository_id for r in result.by_repository] == [world.api.id]


def test_repeat_of_a_bot_thread_in_a_later_run_is_not_counted(uow) -> None:
    world = World()
    world.run(world.api, FROM + dt.timedelta(days=1), 1.0, (SEC, HIGH))
    world.run(world.api, FROM + dt.timedelta(days=2), 1.0, (SEC, HIGH), (SEC, LOW))
    original, repeat, _ = (row for row in world.rows if isinstance(row, Finding))
    world.store(uow)
    with uow:
        uow.findings.mark_covered(repeat.id, original.id)
        uow.commit()

    result = _analytics(uow, world.api)

    assert result.findings_by_day == (
        FindingsDayCount(dt.date(2026, 10, 2), SEC, HIGH, 1),
        FindingsDayCount(dt.date(2026, 10, 3), SEC, LOW, 1),
    )
    assert result.by_repository[0].findings_count == 2


def test_no_runs_give_empty_aggregates_and_no_visibility_gives_no_query(uow, clean_db) -> None:
    World().store(uow)
    assert _analytics(uow, World().api) == EMPTY_ANALYTICS

    statements: list[str] = []

    def record(conn, cursor, statement, *args) -> None:
        statements.append(statement)

    event.listen(clean_db, "before_cursor_execute", record)
    try:
        assert _analytics(uow) == EMPTY_ANALYTICS
    finally:
        event.remove(clean_db, "before_cursor_execute", record)
    assert statements == []
