"""`GitHubInstallationCatalog` на записанных ответах: установки, репозитории, права.

Один `httpx.MockTransport` обслуживает и выдачу installation tokens, и
запросы каталога. Формы ответов — из документации GitHub REST:
`GET /app/installations` (массив), `GET /installation/repositories`
(`{"total_count", "repositories"}`), `GET /repos/{owner}/{repo}/collaborators/{username}/permission`.
"""

from collections.abc import Callable
from typing import Any

import httpx
import jwt
import pytest

from app.domain.entities import InstallationRepository
from app.infrastructure.vcs.errors import VcsAuthError, VcsError, VcsUnavailableError
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.infrastructure.vcs.github_installations import GitHubInstallationCatalog
from tests.infrastructure.conftest import RsaKeyPair

APP_ID = "123456"
FAR_FUTURE = "2099-01-01T00:00:00Z"

Handler = Callable[[httpx.Request], httpx.Response]


def make_catalog(rsa_key_pair: RsaKeyPair, handler: Handler) -> GitHubInstallationCatalog:
    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    auth = GitHubAppAuth(APP_ID, rsa_key_pair.private_pem, client)
    return GitHubInstallationCatalog(auth, client, GitHubVcsGateway(auth, client, sleep=lambda _: None))


def token_response(request: httpx.Request) -> httpx.Response | None:
    """Ответ на выдачу installation token: токен несёт id установки."""
    path = request.url.path
    if path.startswith("/app/installations/") and path.endswith("/access_tokens"):
        installation_id = path.split("/")[3]
        return httpx.Response(201, json={"token": f"tok-{installation_id}", "expires_at": FAR_FUTURE})
    return None


def repo_json(n: int) -> dict[str, Any]:
    return {"id": n, "full_name": f"octo-org/repo-{n}", "default_branch": "main"}


def test_lists_repositories_of_every_installation_page_by_page(rsa_key_pair: RsaKeyPair) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if (token := token_response(request)) is not None:
            return token
        seen.append(f"{request.url.path}?{request.url.query.decode()}")
        if request.url.path == "/app/installations":
            # JWT приложения, подписанный его ключом: iss — App ID.
            bearer = request.headers["Authorization"].removeprefix("Bearer ")
            claims = jwt.decode(bearer, rsa_key_pair.public_pem, algorithms=["RS256"])
            assert claims["iss"] == APP_ID
            page = int(request.url.params["page"])
            # Полная первая страница (100) — значит, есть следующая.
            ids = list(range(1, 101)) if page == 1 else [500]
            return httpx.Response(200, json=[{"id": i} for i in ids])
        if request.url.path == "/installation/repositories":
            installation_token = request.headers["Authorization"].removeprefix("Bearer ")
            page = int(request.url.params["page"])
            if installation_token == "tok-500":
                repos = [repo_json(n) for n in range(1, 101)] if page == 1 else [repo_json(1000)]
                return httpx.Response(200, json={"total_count": 101, "repositories": repos})
            return httpx.Response(200, json={"total_count": 0, "repositories": []})
        raise AssertionError(f"неожиданный запрос {request.url}")

    repositories = make_catalog(rsa_key_pair, handler).list_repositories()

    assert len(repositories) == 101
    assert repositories[0] == InstallationRepository(
        provider_id="1", full_name="octo-org/repo-1", default_branch="main", installation_id=500
    )
    assert repositories[-1].provider_id == "1000"
    assert "/app/installations?per_page=100&page=1" in seen
    assert "/app/installations?per_page=100&page=2" in seen
    assert "/installation/repositories?per_page=100&page=2" in seen


@pytest.mark.parametrize(("permission", "expected"), [("admin", True), ("write", False), ("maintain", False)])
def test_admin_is_the_admin_permission_only(rsa_key_pair: RsaKeyPair, permission: str, expected: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (token := token_response(request)) is not None:
            return token
        assert request.url.path == "/repos/octo-org/api/collaborators/octocat/permission"
        assert request.headers["Authorization"] == "Bearer tok-7"
        return httpx.Response(200, json={"permission": permission, "role_name": permission})

    repository = InstallationRepository("101", "octo-org/api", "main", installation_id=7)
    assert make_catalog(rsa_key_pair, handler).is_admin(repository, "octocat") is expected


def test_a_user_who_is_not_a_collaborator_is_not_admin(rsa_key_pair: RsaKeyPair) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return token_response(request) or httpx.Response(404, json={"message": "Not Found"})

    repository = InstallationRepository("101", "octo-org/api", "main", installation_id=7)
    assert make_catalog(rsa_key_pair, handler).is_admin(repository, "stranger") is False


def test_a_failing_installations_request_is_a_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    with pytest.raises(VcsUnavailableError):
        make_catalog(rsa_key_pair, handler).list_repositories()


def test_a_rejected_app_jwt_is_an_auth_error(rsa_key_pair: RsaKeyPair) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    with pytest.raises(VcsAuthError):
        make_catalog(rsa_key_pair, handler).list_repositories()


def test_a_network_failure_is_unavailable(rsa_key_pair: RsaKeyPair) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети", request=request)

    with pytest.raises(VcsUnavailableError):
        make_catalog(rsa_key_pair, handler).list_repositories()


def test_an_unexpected_body_is_a_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if (token := token_response(request)) is not None:
            return token
        if request.url.path == "/app/installations":
            return httpx.Response(200, json=[{"id": 7}])
        return httpx.Response(200, json={"repositories": [{"id": 1}]})

    with pytest.raises(VcsError):
        make_catalog(rsa_key_pair, handler).list_repositories()


def test_a_failing_permission_request_is_a_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return token_response(request) or httpx.Response(500)

    repository = InstallationRepository("101", "octo-org/api", "main", installation_id=7)
    with pytest.raises(VcsUnavailableError):
        make_catalog(rsa_key_pair, handler).is_admin(repository, "octocat")


def test_a_missing_private_key_is_an_auth_error() -> None:
    """Приложение без ключа (стенд без реквизитов App) — сбой провайдера, а не 500."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("запроса быть не должно")

    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    auth = GitHubAppAuth(APP_ID, "", client)
    catalog = GitHubInstallationCatalog(auth, client, GitHubVcsGateway(auth, client))
    with pytest.raises(VcsAuthError):
        catalog.list_repositories()
