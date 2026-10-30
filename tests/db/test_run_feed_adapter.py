"""Чтение изменённых прогонов для общего потока событий — на настоящей базе."""

import datetime as dt
from uuid import UUID

import pytest

from app.domain.entities import (
    DiffAnchor,
    Finding,
    MergeRequest,
    Repository,
    ReviewRun,
)
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.ids import new_id
from app.domain.run_feed import FeedCursor
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

T0 = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.UTC)


def _at(seconds: float) -> dt.datetime:
    return T0 + dt.timedelta(seconds=seconds)


@pytest.fixture
def uow(clean_db):
    return SqlAlchemyUnitOfWork(clean_db)


def _repository(provider_id: str) -> Repository:
    return Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id=provider_id,
        full_name=f"acme/repo-{provider_id}",
        default_branch="main",
        auto_review_enabled=True,
        created_at=T0,
        updated_at=T0,
        connected_at=T0,
    )


def _merge_request(repository: Repository) -> MergeRequest:
    return MergeRequest(
        id=new_id(),
        repository_id=repository.id,
        number=7,
        title="t",
        description="",
        author="a",
        source_branch="feat",
        target_branch="main",
        head_sha="abc123",
        state=MergeRequestState.OPEN,
        created_at=T0,
        updated_at=T0,
    )


def _run(merge_request: MergeRequest, updated_at: dt.datetime, run_id: UUID | None = None) -> ReviewRun:
    # Итоговый статус: индекс «один активный прогон на коммит» не мешает нескольким прогонам.
    return ReviewRun(
        id=run_id or new_id(),
        merge_request_id=merge_request.id,
        head_sha="abc123",
        status=ReviewRunStatus.COMPLETED,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=updated_at,
        created_at=updated_at,
        updated_at=updated_at,
    )


def _finding(run: ReviewRun, line: int) -> Finding:
    return Finding(
        id=new_id(),
        review_run_id=run.id,
        anchor=DiffAnchor(file_path="app.py", side=DiffSide.NEW, new_line=line),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.HIGH,
        message="m",
        created_at=T0,
        updated_at=T0,
    )


@pytest.fixture
def world(uow):
    """Два репозитория со своими запросами на изменение."""
    seen, other = _repository("1"), _repository("2")
    seen_mr, other_mr = _merge_request(seen), _merge_request(other)
    with uow:
        uow.repositories.add(seen)
        uow.repositories.add(other)
        uow.merge_requests.add(seen_mr)
        uow.merge_requests.add(other_mr)
        uow.commit()
    return seen, other, seen_mr, other_mr


def _store(uow, *runs: ReviewRun, findings: tuple[Finding, ...] = ()) -> None:
    with uow:
        for run in runs:
            uow.review_runs.add(run)
        uow.commit()
    # Отдельной транзакцией: у находки нет relationship к прогону, порядок INSERT в одном
    # flush не гарантирован.
    with uow:
        for finding in findings:
            uow.findings.add(finding)
        uow.commit()


def _changed(uow, after: FeedCursor, recheck_from: dt.datetime, repository_ids, limit: int = 50):
    with uow:
        return uow.review_runs.list_changed(after, recheck_from, repository_ids, limit)


def test_rows_carry_merge_request_repository_and_findings_count(uow, world) -> None:
    seen, _, seen_mr, _ = world
    with_findings = _run(seen_mr, _at(1))
    without = _run(seen_mr, _at(2))
    _store(uow, with_findings, without, findings=(_finding(with_findings, 1), _finding(with_findings, 2)))

    entries = _changed(uow, FeedCursor(T0, UUID(int=0)), T0, {seen.id})

    assert [(e.run.id, e.findings_count) for e in entries] == [(with_findings.id, 2), (without.id, 0)]
    # Время записи запроса на изменение ставит база — сверяем то, что пришло из него.
    assert (entries[0].merge_request.id, entries[0].merge_request.number) == (seen_mr.id, seen_mr.number)
    assert (entries[0].repository.id, entries[0].repository.full_name) == (seen.id, seen.full_name)


def test_only_runs_of_the_given_repositories(uow, world) -> None:
    seen, _, seen_mr, other_mr = world
    visible = _run(seen_mr, _at(1))
    _store(uow, visible, _run(other_mr, _at(1)))

    assert [e.run.id for e in _changed(uow, FeedCursor(T0, UUID(int=0)), T0, {seen.id})] == [visible.id]
    assert _changed(uow, FeedCursor(T0, UUID(int=0)), T0, set()) == []


def test_the_cursor_is_a_pair_and_keeps_a_run_with_the_same_updated_at(uow, world) -> None:
    seen, _, seen_mr, _ = world
    low, high = _run(seen_mr, _at(1), UUID(int=1)), _run(seen_mr, _at(1), UUID(int=2))
    _store(uow, low, high)

    entries = _changed(uow, FeedCursor(_at(1), low.id), _at(1), {seen.id})

    assert [e.run.id for e in entries] == [high.id]


def test_the_recheck_window_returns_rows_at_or_below_the_cursor(uow, world) -> None:
    seen, _, seen_mr, _ = world
    too_old = _run(seen_mr, _at(-10))
    late = _run(seen_mr, _at(-2))
    at_cursor = _run(seen_mr, _at(0), UUID(int=1))
    fresh = _run(seen_mr, _at(1))
    _store(uow, too_old, late, at_cursor, fresh)

    entries = _changed(uow, FeedCursor(_at(0), UUID(int=1)), _at(-5), {seen.id})

    assert [e.run.id for e in entries] == [late.id, at_cursor.id, fresh.id]


def test_the_limit_bounds_only_the_part_above_the_cursor(uow, world) -> None:
    """Окно перепроверки читается целиком: запоздавшая строка за `limit` уже виденными не теряется."""
    seen, _, seen_mr, _ = world
    window = [_run(seen_mr, _at(-1 + n / 10)) for n in range(3)]
    fresh = [_run(seen_mr, _at(1 + n)) for n in range(3)]
    _store(uow, *window, *fresh)

    entries = _changed(uow, FeedCursor(_at(0), UUID(int=0)), _at(-5), {seen.id}, limit=2)

    assert [e.run.id for e in entries] == [*(w.id for w in window), fresh[0].id, fresh[1].id]


def test_repositories_by_provider_ids(uow, world) -> None:
    seen, other, _, _ = world

    with uow:
        found = uow.repositories.list_by_provider_ids(Provider.GITHUB, ["1", "2", "404"])
        none = uow.repositories.list_by_provider_ids(Provider.GITHUB, [])

    assert {r.id for r in found} == {seen.id, other.id}
    assert none == []
