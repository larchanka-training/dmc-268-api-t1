"""Контракт `/api/v1/repositories`. Без БД — фейковый `UnitOfWork` и фейковый каталог установок."""

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_installation_catalog,
    get_unit_of_work,
    get_unit_of_work_factory,
)
from app.api.factory import create_app
from app.api.pagination import MAX_LIMIT
from app.config import Settings
from app.domain.entities import InstallationRepository, Repository
from app.domain.enums import Provider, RepositoryRole
from app.domain.ids import new_id
from app.domain.vcs_errors import VcsError

from .fakes.auth import SIGNED_IN, sign_in
from .fakes.installation_catalog import FakeInstallationCatalog
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


ORIGIN = "https://review.example"
API = InstallationRepository(provider_id="101", full_name="octo-org/api", default_branch="main", installation_id=7)
WEB = InstallationRepository(provider_id="202", full_name="octo-org/web", default_branch="trunk", installation_id=7)


def _client(
    uow: FakeUnitOfWork, catalog: FakeInstallationCatalog | None = None, *, signed_in: bool = True
) -> TestClient:
    app = create_app(SETTINGS.model_copy(update={"public_origin": ORIGIN}))
    if signed_in:
        sign_in(app)

    def override_uow() -> Iterator[FakeUnitOfWork]:
        yield uow

    app.dependency_overrides[get_unit_of_work] = override_uow
    app.dependency_overrides[get_unit_of_work_factory] = lambda: lambda: uow
    app.dependency_overrides[get_installation_catalog] = lambda: catalog or FakeInstallationCatalog()
    return TestClient(app, base_url=ORIGIN)


def _connect(client: TestClient, provider_id: str = "101", provider: str = "github") -> httpx.Response:
    response: httpx.Response = client.post(
        "/api/v1/repositories", json={"provider": provider, "provider_id": provider_id}, headers={"Origin": ORIGIN}
    )
    return response


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


def test_a_non_integer_limit_is_a_validation_error_without_the_input() -> None:
    response = _client(FakeUnitOfWork()).get("/api/v1/repositories", params={"limit": "<b>abc</b>"})

    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"] == [{"where": "query.limit", "reason": "ожидается целое число"}]
    assert "abc" not in response.text


def test_a_limit_below_one_is_a_validation_error() -> None:
    response = _client(FakeUnitOfWork()).get("/api/v1/repositories", params={"limit": 0})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["errors"] == [{"where": "query.limit", "reason": "должно быть не меньше 1"}]


def test_available_repositories_are_marked_when_already_connected() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(_repository(101))
    managers = {("octo-org/api", SIGNED_IN.login), ("octo-org/web", SIGNED_IN.login)}
    client = _client(uow, FakeInstallationCatalog([API, WEB], managers=managers))

    response = client.get("/api/v1/repositories/available")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "provider": "github",
                "provider_id": "101",
                "full_name": "octo-org/api",
                "default_branch": "main",
                "already_connected": True,
            },
            {
                "provider": "github",
                "provider_id": "202",
                "full_name": "octo-org/web",
                "default_branch": "trunk",
                "already_connected": False,
            },
        ]
    }


def test_a_read_only_user_sees_the_repository_but_cannot_connect_it() -> None:
    catalog = FakeInstallationCatalog([API, WEB], roles={("octo-org/web", SIGNED_IN.login): RepositoryRole.READ})
    uow = FakeUnitOfWork()
    client = _client(uow, catalog)

    listed = client.get("/api/v1/repositories/available")
    connected = _connect(client, WEB.provider_id)

    assert listed.status_code == 200
    assert [item["full_name"] for item in listed.json()["items"]] == ["octo-org/web"]
    assert connected.status_code == 403
    assert connected.json()["code"] == "REPOSITORY_MANAGE_REQUIRED"
    assert uow.repositories.stored == {}


def test_a_maintainer_connects_a_repository() -> None:
    catalog = FakeInstallationCatalog([API], roles={("octo-org/api", SIGNED_IN.login): RepositoryRole.MAINTAIN})

    assert _connect(_client(FakeUnitOfWork(), catalog)).status_code == 201


def test_available_repositories_are_empty_without_access_anywhere() -> None:
    response = _client(FakeUnitOfWork(), FakeInstallationCatalog([API, WEB])).get("/api/v1/repositories/available")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_available_repositories_answer_502_when_the_permission_check_fails() -> None:
    catalog = FakeInstallationCatalog([API], permission_error=VcsError("GitHub API: HTTP 503"))

    response = _client(FakeUnitOfWork(), catalog).get("/api/v1/repositories/available")

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"


def test_available_repositories_answer_502_when_github_fails() -> None:
    client = _client(FakeUnitOfWork(), FakeInstallationCatalog(error=VcsError("GitHub API: HTTP 503")))

    response = client.get("/api/v1/repositories/available")

    assert response.status_code == 502
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "SCM_UNAVAILABLE"


def test_available_repositories_require_a_session() -> None:
    response = _client(FakeUnitOfWork(), FakeInstallationCatalog([API]), signed_in=False).get(
        "/api/v1/repositories/available"
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


def test_a_manager_connects_a_repository() -> None:
    uow = FakeUnitOfWork()
    client = _client(uow, FakeInstallationCatalog([API, WEB], managers={("octo-org/web", SIGNED_IN.login)}))

    response = _connect(client, "202")

    assert response.status_code == 201
    body = response.json()
    assert body["provider"] == "github"
    assert body["provider_id"] == "202"
    assert body["full_name"] == "octo-org/web"
    assert body["default_branch"] == "trunk"
    assert body["auto_review_enabled"] is True
    assert body["created_at"].endswith("Z")
    stored = uow.repositories.find_by_provider(Provider.GITHUB, "202")
    assert stored is not None
    assert str(stored.id) == body["id"]


def test_connecting_without_manage_rights_is_403() -> None:
    uow = FakeUnitOfWork()
    response = _connect(_client(uow, FakeInstallationCatalog([API])))

    assert response.status_code == 403
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "REPOSITORY_MANAGE_REQUIRED"
    assert uow.repositories.stored == {}


def test_connecting_a_repository_outside_the_installation_is_404() -> None:
    response = _connect(_client(FakeUnitOfWork(), FakeInstallationCatalog([API])), "999")

    assert response.status_code == 404
    assert response.json()["code"] == "REPOSITORY_NOT_IN_INSTALLATION"


def test_connecting_a_gitlab_repository_is_404() -> None:
    response = _connect(_client(FakeUnitOfWork(), FakeInstallationCatalog([API])), provider="gitlab")

    assert response.status_code == 404
    assert response.json()["code"] == "REPOSITORY_NOT_IN_INSTALLATION"


def test_connecting_twice_is_409() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(_repository(101))
    client = _client(uow, FakeInstallationCatalog([API], managers={("octo-org/api", SIGNED_IN.login)}))

    response = _connect(client)

    assert response.status_code == 409
    assert response.json()["code"] == "REPOSITORY_ALREADY_CONNECTED"


def test_connecting_a_connected_repository_without_manage_rights_is_403_not_409() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(_repository(101))

    response = _connect(_client(uow, FakeInstallationCatalog([API])))

    assert response.status_code == 403
    assert response.json()["code"] == "REPOSITORY_MANAGE_REQUIRED"


def test_connecting_answers_502_when_github_fails() -> None:
    uow = FakeUnitOfWork()
    response = _connect(_client(uow, FakeInstallationCatalog(error=VcsError("GitHub API: HTTP 503"))))

    assert response.status_code == 502
    assert response.json()["code"] == "SCM_UNAVAILABLE"
    assert uow.repositories.stored == {}


def test_connecting_requires_a_session() -> None:
    response = _connect(_client(FakeUnitOfWork(), FakeInstallationCatalog([API]), signed_in=False))

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
