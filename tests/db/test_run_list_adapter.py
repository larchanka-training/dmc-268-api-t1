"""Страница списка прогонов GET /reviews — на настоящей базе: SQL фильтров, порядок, total."""

import datetime as dt
from uuid import UUID

import pytest

from app.domain.entities import DiffAnchor, Finding, MergeRequest, Repository, ReviewRun
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
from app.domain.run_list import RunListFilter, run_list_filter
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

T0 = dt.datetime(2026, 10, 23, 12, 0, tzinfo=dt.UTC)


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
    )


def _merge_request(repository: Repository, number: int, title: str, author: str = "a", branch: str = "feat") -> MergeRequest:
    return MergeRequest(
        id=new_id(),
        repository_id=repository.id,
        number=number,
        title=title,
        description="",
        author=author,
        source_branch=branch,
        target_branch="main",
        head_sha="abc123",
        state=MergeRequestState.OPEN,
        created_at=T0,
        updated_at=T0,
    )


def _run(
    merge_request: MergeRequest,
    minutes: int,
    status: ReviewRunStatus = ReviewRunStatus.COMPLETED,
    run_id: UUID | None = None,
) -> ReviewRun:
    at = T0 + dt.timedelta(minutes=minutes)
    return ReviewRun(
        id=run_id or new_id(),
        merge_request_id=merge_request.id,
        head_sha=f"sha{minutes}",
        status=status,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=at,
        created_at=at,
        updated_at=at,
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


def _store(uow, *rows: Repository | MergeRequest | ReviewRun | Finding) -> None:
    # По транзакции на вид записи: порядок INSERT в одном flush не гарантирован.
    for kind, repo in ((Repository, "repositories"), (MergeRequest, "merge_requests"), (ReviewRun, "review_runs"), (Finding, "findings")):
        with uow:
            for row in rows:
                if isinstance(row, kind):
                    getattr(uow, repo).add(row)
            uow.commit()


def _page(uow, repositories: set[UUID], limit: int = 50, offset: int = 0, **params):
    run_filter = run_list_filter(
        visible=repositories,
        repository_ids=params.get("repository_ids"),
        statuses=params.get("statuses"),
        active=params.get("active"),
        author=params.get("author"),
        q=params.get("q"),
        created_from=params.get("created_from"),
        created_to=params.get("created_to"),
    )
    with uow:
        return uow.review_runs.list_page(run_filter, limit, offset)


@pytest.fixture
def world(uow):
    """Два репозитория; у видимого — запросы с разными заголовками, авторами и ветками."""
    seen, other = _repository("1"), _repository("2")
    cache = _merge_request(seen, 42, "Кэш ответов", author="alice")
    percent = _merge_request(seen, 7, "Скидка 100% на всё", branch="hotfix/promo_code")
    hidden = _merge_request(other, 42, "Кэш в другом репозитории")
    _store(uow, seen, other, cache, percent, hidden)
    return seen, other, cache, percent, hidden


def test_page_is_newest_first_with_findings_and_full_total(uow, world) -> None:
    seen, _, cache, percent, hidden = world
    old = _run(cache, 0)
    tie_low, tie_high = _run(percent, 5, run_id=UUID(int=1)), _run(cache, 5, run_id=UUID(int=2))
    new = _run(percent, 10)
    _store(uow, old, tie_low, tie_high, new, _run(hidden, 20), _finding(tie_high, 1), _finding(tie_high, 2))

    page = _page(uow, {seen.id})
    assert [e.run.id for e in page.entries] == [new.id, tie_high.id, tie_low.id, old.id]
    assert [e.findings_count for e in page.entries] == [0, 2, 0, 0]
    assert (page.entries[1].merge_request.number, page.entries[1].repository.id) == (42, seen.id)
    assert page.total == 4

    second = _page(uow, {seen.id}, limit=2, offset=1)
    assert [e.run.id for e in second.entries] == [tie_high.id, tie_low.id]
    assert second.total == 4


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("кэш", {"cache"}),
        ("КЭШ ОТВ", {"cache"}),
        ("ALICE", {"cache"}),
        ("promo_", {"percent"}),
        ("100%", {"percent"}),
        ("_", {"percent"}),
        ("%", {"percent"}),
        ("#42", {"cache"}),
        ("7", {"percent"}),
        ("4", set()),
    ],
)
def test_search_is_case_insensitive_escaped_and_matches_exact_number(uow, world, q: str, expected: set[str]) -> None:
    """`%` и `_` — символы, а не шаблон; кириллица без учёта регистра; номер — точный, не подстрока."""
    seen, _, cache, percent, hidden = world
    runs = {"cache": _run(cache, 0), "percent": _run(percent, 1)}
    _store(uow, *runs.values(), _run(hidden, 2))

    page = _page(uow, {seen.id}, q=q)

    assert {name for name, run in runs.items() if run.id in {e.run.id for e in page.entries}} == expected
    assert page.total == len(expected)


def test_filters_combine_with_and(uow, world) -> None:
    seen, other, cache, percent, hidden = world
    queued = _run(cache, 0, ReviewRunStatus.QUEUED)
    failed = _run(cache, 10, ReviewRunStatus.FAILED)
    _store(uow, queued, failed, _run(percent, 20, ReviewRunStatus.QUEUED), _run(hidden, 30, ReviewRunStatus.QUEUED))
    visible = {seen.id, other.id}

    active = _page(uow, visible, active=True, author="alice")
    assert [e.run.id for e in active.entries] == [queued.id]
    period = _page(uow, visible, author="alice", created_from=T0 + dt.timedelta(minutes=10), created_to=T0 + dt.timedelta(minutes=11))
    assert [e.run.id for e in period.entries] == [failed.id]
    assert _page(uow, visible, repository_ids={other.id}, statuses={ReviewRunStatus.FAILED}).total == 0


def test_empty_visibility_or_statuses_answer_without_query(uow, world) -> None:
    seen, *_ = world
    with uow:
        assert uow.review_runs.list_page(RunListFilter(repository_ids=frozenset()), 50, 0).total == 0
        empty = RunListFilter(repository_ids=frozenset({seen.id}), statuses=frozenset())
        assert uow.review_runs.list_page(empty, 50, 0).entries == []
