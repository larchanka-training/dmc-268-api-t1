"""Контракт `GET /api/v1/repositories/{id}/pull-requests`. Без БД."""

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.dependencies import get_installation_catalog, get_token_provider
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import (
    InstallationRepository,
    MergeRequest,
    Repository,
    RepositoryPermissions,
)
from app.domain.enums import MergeRequestState, Provider
from app.domain.ids import new_id
from app.domain.vcs_errors import VcsError

from .fakes.auth import sign_in_with_session
from .fakes.installation_catalog import FakeInstallationCatalog
from .fakes.oauth import FakeOAuthProvider
from .fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)

NOW = datetime(2026, 9, 29, tzinfo=UTC)


READ = RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)
# GitHub по токену пользователя отдаёт репозиторий тестов (`provider_id` 1) — он виден.
VISIBLE = InstallationRepository("1", "owner/repo", "main", READ)


def _client(uow: FakeUnitOfWork, catalog: FakeInstallationCatalog | None = None) -> TestClient:
    app = create_app(SETTINGS)
    cookies = sign_in_with_session(app, uow)
    app.dependency_overrides[get_installation_catalog] = lambda: catalog or FakeInstallationCatalog([VISIBLE])
    app.dependency_overrides[get_token_provider] = lambda: FakeOAuthProvider()
    return TestClient(app, cookies=cookies)


def _repository_with_a_pull_request(uow: FakeUnitOfWork) -> Repository:
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
    return repo


def test_returns_pull_requests_for_an_existing_repository() -> None:
    uow = FakeUnitOfWork()
    repo = _repository_with_a_pull_request(uow)
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
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "REPOSITORY_NOT_FOUND"


def test_pull_requests_of_a_repository_hidden_on_github_are_404_like_a_missing_one() -> None:
    """Заголовки и описания PR чужого репозитория не отдаются: тот же `404`, что у несуществующего."""
    uow = FakeUnitOfWork()
    repo = _repository_with_a_pull_request(uow)
    hidden = _client(uow, FakeInstallationCatalog([InstallationRepository("999", "owner/other", "main", READ)]))

    response = hidden.get(f"/api/v1/repositories/{repo.id}/pull-requests")
    missing = hidden.get(f"/api/v1/repositories/{new_id()}/pull-requests")

    assert response.status_code == missing.status_code == 404
    assert response.json()["code"] == "REPOSITORY_NOT_FOUND"
    assert response.json()["detail"] == missing.json()["detail"]


def test_github_failure_on_pull_requests_is_502() -> None:
    uow = FakeUnitOfWork()
    repo = _repository_with_a_pull_request(uow)

    response = _client(uow, FakeInstallationCatalog(error=VcsError("GitHub недоступен"))).get(
        f"/api/v1/repositories/{repo.id}/pull-requests"
    )

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"


def test_an_invalid_limit_does_not_ask_github() -> None:
    catalog = FakeInstallationCatalog([VISIBLE])

    response = _client(FakeUnitOfWork(), catalog).get(
        f"/api/v1/repositories/{new_id()}/pull-requests", params={"limit": 0}
    )

    assert response.status_code == 422
    assert catalog.calls == []
