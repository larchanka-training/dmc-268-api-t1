"""Пагинация чтения: `RepositoryRepo.list_all`, `MergeRequestRepo.list_for_repository`."""

import datetime as dt

import pytest

from app.domain.entities import MergeRequest, Repository
from app.domain.enums import MergeRequestState, Provider
from app.domain.ids import new_id
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)


@pytest.fixture
def uow(clean_db):
    return SqlAlchemyUnitOfWork(clean_db)


def a_repository(*, provider_id: str, created_at: dt.datetime) -> Repository:
    return Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id=provider_id,
        full_name=f"acme/repo-{provider_id}",
        default_branch="main",
        auto_review_enabled=True,
        created_at=created_at,
        updated_at=created_at,
    )


def a_merge_request(repository_id, *, number: int) -> MergeRequest:
    return MergeRequest(
        id=new_id(),
        repository_id=repository_id,
        number=number,
        title=f"PR {number}",
        description="",
        author="a",
        source_branch="feat",
        target_branch="main",
        head_sha="abc123",
        state=MergeRequestState.OPEN,
        created_at=NOW,
        updated_at=NOW,
    )


def test_list_all_orders_by_created_at_and_slices(uow) -> None:
    with uow as work:
        for i in range(3):
            work.repositories.add(
                a_repository(provider_id=str(i), created_at=NOW + dt.timedelta(minutes=i))
            )
        work.commit()

    with uow as work:
        first_page = work.repositories.list_all(limit=2, offset=0)
        second_page = work.repositories.list_all(limit=2, offset=2)

    assert [r.provider_id for r in first_page] == ["0", "1"]
    assert [r.provider_id for r in second_page] == ["2"]


def test_list_for_repository_only_returns_its_own_merge_requests(uow) -> None:
    with uow as work:
        repo_a = a_repository(provider_id="a", created_at=NOW)
        repo_b = a_repository(provider_id="b", created_at=NOW)
        work.repositories.add(repo_a)
        work.repositories.add(repo_b)
        work.merge_requests.add(a_merge_request(repo_a.id, number=2))
        work.merge_requests.add(a_merge_request(repo_a.id, number=1))
        work.merge_requests.add(a_merge_request(repo_b.id, number=1))
        work.commit()

    with uow as work:
        for_a = work.merge_requests.list_for_repository(repo_a.id, limit=50, offset=0)

    assert [mr.number for mr in for_a] == [1, 2]


def test_list_for_repository_slices(uow) -> None:
    with uow as work:
        repo = a_repository(provider_id="c", created_at=NOW)
        work.repositories.add(repo)
        for number in (1, 2, 3):
            work.merge_requests.add(a_merge_request(repo.id, number=number))
        work.commit()

    with uow as work:
        page = work.merge_requests.list_for_repository(repo.id, limit=1, offset=1)

    assert [mr.number for mr in page] == [2]


def test_list_all_breaks_created_at_ties_by_id_so_pages_neither_repeat_nor_skip(uow) -> None:
    """Три строки с одним `created_at`: порядок задаёт только тай-брейкер `id`."""
    with uow as work:
        repositories = [a_repository(provider_id=str(i), created_at=NOW) for i in range(3)]
        # Вставка против порядка `id`: без тай-брейкера Postgres отдал бы строки
        # в порядке вставки, и тест это заметил бы.
        for repository in sorted(repositories, key=lambda r: r.id, reverse=True):
            work.repositories.add(repository)
        work.commit()

    with uow as work:
        pages = [work.repositories.list_all(limit=1, offset=offset) for offset in range(4)]

    paged_ids = [r.id for page in pages for r in page]
    assert paged_ids == sorted(r.id for r in repositories)
