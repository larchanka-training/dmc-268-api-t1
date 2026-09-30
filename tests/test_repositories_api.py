"""Контракт `GET /api/v1/repositories`. Без БД — фейковый `UnitOfWork`."""

from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.dependencies import get_unit_of_work
from app.api.factory import create_app
from app.api.repositories import MAX_LIMIT
from app.config import Settings
from app.domain.entities import Repository
from app.domain.enums import Provider
from app.domain.ids import new_id

from .fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _repository(n: int) -> Repository:
    return Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id=str(n),
        full_name=f"owner/repo-{n}",
        default_branch="main",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    )


def _client(uow: FakeUnitOfWork) -> TestClient:
    app = create_app(SETTINGS)

    def override_uow() -> Iterator[FakeUnitOfWork]:
        yield uow

    app.dependency_overrides[get_unit_of_work] = override_uow
    return TestClient(app)


def test_returns_registered_repositories() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(_repository(1))
    uow.repositories.add(_repository(2))
    client = _client(uow)

    response = client.get("/api/v1/repositories")

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert {item["full_name"] for item in body["items"]} == {"owner/repo-1", "owner/repo-2"}


def test_limit_above_the_ceiling_is_capped_not_rejected() -> None:
    uow = FakeUnitOfWork()
    for n in range(MAX_LIMIT + 5):
        uow.repositories.add(_repository(n))
    client = _client(uow)

    response = client.get("/api/v1/repositories", params={"limit": 10_000})

    assert response.status_code == 200
    assert len(response.json()["items"]) == MAX_LIMIT
