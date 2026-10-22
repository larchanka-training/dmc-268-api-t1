"""`GitHubInstallationCatalog` на записанных ответах GitHub REST по токену пользователя.

Формы ответов — из документации GitHub REST (версия API 2022-11-28):

- «List app installations accessible to the user access token»,
  `GET /user/installations` — `{"total_count", "installations": [{"id", "account", …,
  "suspended_at"}]}`;
- «List repositories accessible to the user access token»,
  `GET /user/installations/{installation_id}/repositories` — `{"total_count",
  "repository_selection", "repositories": [{"id", "full_name", "default_branch",
  "private", "permissions": {"admin", "maintain", "push", "triage", "pull"}, …}]}`.

https://docs.github.com/en/rest/apps/installations — раздел «Installations» REST API.
Против настоящего GitHub не проверено: это делает проверка на стенде.
"""

import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.domain.entities import InstallationRepository, RepositoryPermissions, Secret
from app.domain.provider_token import ProviderTokenRejected
from app.infrastructure.vcs.errors import VcsError, VcsUnavailableError
from app.infrastructure.vcs.github_installations import GitHubInstallationCatalog

TOKEN = Secret("ghu_user_token_value")
Handler = Callable[[httpx.Request], httpx.Response]


def make_catalog(handler: Handler) -> GitHubInstallationCatalog:
    return GitHubInstallationCatalog(httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler)))


def installation(installation_id: int, suspended_at: str | None = None) -> dict[str, Any]:
    """Установка из примера ответа документации, урезанная до полей, что читает адаптер, и соседей."""
    return {
        "id": installation_id,
        "account": {"login": "octo-org", "id": 1, "type": "Organization"},
        "app_id": 1,
        "target_type": "Organization",
        "repository_selection": "selected",
        "suspended_at": suspended_at,
        "suspended_by": None,
    }


def repo_json(n: int, *, admin: bool = False, maintain: bool = False) -> dict[str, Any]:
    return {
        "id": n,
        "node_id": "MDEwOlJlcG9zaXRvcnkxMjk2MjY5",
        "name": f"repo-{n}",
        "full_name": f"octo-org/repo-{n}",
        "private": True,
        "default_branch": "main",
        "permissions": {"admin": admin, "maintain": maintain, "push": admin or maintain, "triage": True, "pull": True},
    }


def router(installations: list[dict[str, Any]], repositories: dict[int, list[dict[str, Any]]], seen: list[str] | None = None) -> Handler:
    """Ответы GitHub постранично по `per_page` и `page` из запроса."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(f"{request.url.path}?{request.url.query.decode()}")
        assert request.headers["Authorization"] == "Bearer ghu_user_token_value"
        per_page = int(request.url.params["per_page"])
        page = int(request.url.params["page"])
        window = slice((page - 1) * per_page, page * per_page)
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"total_count": len(installations), "installations": installations[window]})
        installation_id = int(request.url.path.split("/")[3])
        items = repositories[installation_id]
        return httpx.Response(200, json={"total_count": len(items), "repository_selection": "selected", "repositories": items[window]})

    return handler


def test_lists_repositories_of_every_installation_with_user_permissions() -> None:
    catalog = make_catalog(router([installation(7), installation(8)], {7: [repo_json(1, admin=True)], 8: [repo_json(2, maintain=True), repo_json(3)]}))

    assert catalog.list_repositories(TOKEN) == [
        InstallationRepository("1", "octo-org/repo-1", "main", RepositoryPermissions(admin=True, maintain=False)),
        InstallationRepository("2", "octo-org/repo-2", "main", RepositoryPermissions(admin=False, maintain=True)),
        InstallationRepository("3", "octo-org/repo-3", "main", RepositoryPermissions(admin=False, maintain=False)),
    ]


def test_pages_by_100_until_a_short_page() -> None:
    seen: list[str] = []
    installations = [installation(i) for i in range(1, 102)]
    repositories: dict[int, list[dict[str, Any]]] = {i: [] for i in range(1, 102)}
    repositories[1] = [repo_json(n) for n in range(250)]

    result = make_catalog(router(installations, repositories, seen)).list_repositories(TOKEN)

    assert len(result) == 250
    assert seen[:2] == ["/user/installations?per_page=100&page=1", "/user/installations?per_page=100&page=2"]
    assert [s for s in seen if s.startswith("/user/installations/1/")] == [
        "/user/installations/1/repositories?per_page=100&page=1",
        "/user/installations/1/repositories?per_page=100&page=2",
        "/user/installations/1/repositories?per_page=100&page=3",
    ]


def test_a_suspended_installation_is_skipped_not_fatal() -> None:
    seen: list[str] = []
    catalog = make_catalog(router([installation(7, suspended_at="2026-10-01T00:00:00Z"), installation(8)], {8: [repo_json(2)]}, seen))

    assert [r.full_name for r in catalog.list_repositories(TOKEN)] == ["octo-org/repo-2"]
    assert not any(s.startswith("/user/installations/7/") for s in seen)


def test_an_installation_gone_between_requests_is_skipped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"total_count": 2, "installations": [installation(7), installation(8)]})
        if request.url.path.startswith("/user/installations/7/"):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(200, json={"total_count": 1, "repositories": [repo_json(2)]})

    assert [r.provider_id for r in make_catalog(handler).list_repositories(TOKEN)] == ["2"]


def test_permissions_without_maintain_as_in_the_docs_example_mean_no_maintain() -> None:
    item = repo_json(1)
    item["permissions"] = {"admin": False, "push": False, "pull": True}
    catalog = make_catalog(router([installation(7)], {7: [item]}))

    assert catalog.list_repositories(TOKEN)[0].permissions == RepositoryPermissions(admin=False, maintain=False)


def test_a_401_means_the_token_is_rejected() -> None:
    catalog = make_catalog(lambda request: httpx.Response(401, json={"message": "Bad credentials"}))

    with pytest.raises(ProviderTokenRejected):
        catalog.list_repositories(TOKEN)


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_statuses_are_unavailable(status: int) -> None:
    with pytest.raises(VcsUnavailableError):
        make_catalog(lambda request: httpx.Response(status)).list_repositories(TOKEN)


@pytest.mark.parametrize("status", [403, 404])
def test_other_errors_on_the_installation_list_are_vcs_errors(status: int) -> None:
    with pytest.raises(VcsError):
        make_catalog(lambda request: httpx.Response(status)).list_repositories(TOKEN)


def test_network_failure_is_unavailable_and_carries_no_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(VcsUnavailableError) as caught:
        make_catalog(handler).list_repositories(TOKEN)
    assert "ghu_user_token_value" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def _with_repo(mutate: Callable[[dict[str, Any]], None]) -> Handler:
    item = repo_json(1)
    mutate(item)
    return router([installation(7)], {7: [item]})


@pytest.mark.parametrize(
    "handler",
    [
        _with_repo(lambda item: item.update(full_name=None)),
        _with_repo(lambda item: item.update(default_branch=None)),
        _with_repo(lambda item: item.update(id=None)),
        _with_repo(lambda item: item.update(id=True)),
        _with_repo(lambda item: item.update(permissions=None)),
        _with_repo(lambda item: item.pop("permissions")),
        _with_repo(lambda item: item["permissions"].update(admin=None)),
        _with_repo(lambda item: item["permissions"].update(maintain="yes")),
        lambda request: httpx.Response(200, json={"total_count": 1, "installations": None}),
        lambda request: httpx.Response(200, json={"total_count": 1, "installations": [{"id": None}]}),
        lambda request: httpx.Response(200, json=[installation(7)]),
        lambda request: httpx.Response(200, content=b"not json"),
        lambda request: (
            httpx.Response(200, json={"total_count": 1, "installations": [installation(7)]})
            if request.url.path == "/user/installations"
            else httpx.Response(200, json={"total_count": 1, "repositories": [None]})
        ),
    ],
)
def test_an_unexpected_body_is_a_vcs_error_not_a_crash(handler: Handler) -> None:
    with pytest.raises(VcsError) as caught:
        make_catalog(handler).list_repositories(TOKEN)
    assert "ghu_user_token_value" not in str(caught.value)


def test_an_installation_closed_for_the_token_is_skipped_not_fatal() -> None:
    """`403` на репозиториях одной установки — например, SSO организации не
    авторизован для токена: остальные установки видны, список не падает."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"total_count": 2, "installations": [installation(7), installation(8)]})
        if request.url.path.startswith("/user/installations/7/"):
            return httpx.Response(403, json={"message": "Resource protected by organization SAML enforcement."})
        return httpx.Response(200, json={"total_count": 1, "repositories": [repo_json(2)]})

    assert [r.provider_id for r in make_catalog(handler).list_repositories(TOKEN)] == ["2"]


@pytest.mark.parametrize(
    "headers",
    [{"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1700000000"}, {"retry-after": "60"}],
)
@pytest.mark.parametrize("path", ["/user/installations", "/user/installations/7/repositories"])
def test_a_rate_limit_403_is_unavailable_not_skipped(headers: dict[str, str], path: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == path:
            return httpx.Response(403, headers=headers, json={"message": "API rate limit exceeded"})
        return httpx.Response(200, json={"total_count": 1, "installations": [installation(7)]})

    with pytest.raises(VcsUnavailableError):
        make_catalog(handler).list_repositories(TOKEN)


def test_paging_stops_at_total_count_without_an_extra_request() -> None:
    seen: list[str] = []
    catalog = make_catalog(router([installation(7)], {7: [repo_json(n) for n in range(100)]}, seen))

    assert len(catalog.list_repositories(TOKEN)) == 100
    assert seen == ["/user/installations?per_page=100&page=1", "/user/installations/7/repositories?per_page=100&page=1"]


def test_paging_never_exceeds_fifty_pages(caplog: pytest.LogCaptureFixture) -> None:
    """Ответ, который не кончается (total_count растёт, страницы полные), не держит запрос вечно."""
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"total_count": 1, "installations": [installation(7)]})
        pages.append(request.url.params["page"])
        return httpx.Response(200, json={"total_count": 10**9, "repositories": [repo_json(n) for n in range(100)]})

    with caplog.at_level(logging.WARNING):
        result = make_catalog(handler).list_repositories(TOKEN)

    assert len(pages) == 50
    assert len(result) == 5000
    assert "усечён" in caplog.text


def test_a_page_without_total_count_is_unexpected() -> None:
    with pytest.raises(VcsError):
        make_catalog(lambda request: httpx.Response(200, json={"installations": []})).list_repositories(TOKEN)
