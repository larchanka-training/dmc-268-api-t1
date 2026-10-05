"""Контракт `GET /api/v1/repositories/{id}/pull-requests`. Без БД."""

from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.dependencies import get_unit_of_work
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import MergeRequest, Repository
from app.domain.enums import MergeRequestState, Provider
from app.domain.ids import new_id

from .fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _client(uow: FakeUnitOfWork) -> TestClient:
    app = create_app(SETTINGS)

    def override_uow() -> Iterator[FakeUnitOfWork]:
        yield uow

    app.dependency_overrides[get_unit_of_work] = override_uow
    return TestClient(app)


def test_returns_pull_requests_for_an_existing_repository() -> None:
    uow = FakeUnitOfWork()
    repo = Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id="1",
        full_name="owner/repo",
        default_branch="main",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    )
    uow.repositories.add(repo)
    uow.merge_requests.add(
        MergeRequest(
            id=new_id(),
            repository_id=repo.id,
            number=1,
            title="Fix",
            description="",
            author="octocat",
            source_branch="feature",
            target_branch="main",
            head_sha="abc123",
            state=MergeRequestState.OPEN,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    client = _client(uow)

    response = client.get(f"/api/v1/repositories/{repo.id}/pull-requests")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["number"] == 1


def test_unknown_repository_is_404() -> None:
    uow = FakeUnitOfWork()
    client = _client(uow)

    response = client.get(f"/api/v1/repositories/{new_id()}/pull-requests")

    assert response.status_code == 404
