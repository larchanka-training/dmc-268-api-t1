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
import jwt
import pytest

from app.domain.entities import InstallationRepository, RepositoryPermissions, Secret
from app.domain.enums import RepositorySelection
from app.domain.provider_token import ProviderTokenRejected
from app.infrastructure.vcs.errors import VcsAuthError, VcsError, VcsUnavailableError
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.infrastructure.vcs.github_installations import GitHubInstallationCatalog
from tests.infrastructure.conftest import RsaKeyPair

TOKEN = Secret("ghu_user_token_value")
Handler = Callable[[httpx.Request], httpx.Response]


def make_catalog(handler: Handler, app_auth: GitHubAppAuth | None = None) -> GitHubInstallationCatalog:
    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    return GitHubInstallationCatalog(client, app_auth or _UnusedAppAuth())  # type: ignore[arg-type]


class _UnusedAppAuth:
    """Списку JWT приложения не нужен: вызов — ошибка теста."""

    def app_jwt(self) -> str:
        raise AssertionError("JWT приложения не должен запрашиваться")


def installation(installation_id: int, suspended_at: str | None = None, selection: str = "selected") -> dict[str, Any]:
    """Установка из примера ответа документации, урезанная до полей, что читает адаптер, и соседей."""
    return {
        "id": installation_id,
        "account": {"login": "octo-org", "id": 1, "type": "Organization"},
        "app_id": 1,
        "target_type": "Organization",
        "repository_selection": selection,
        "suspended_at": suspended_at,
        "suspended_by": None,
    }


def repo_json(n: int, *, admin: bool = False, maintain: bool = False, push: bool = False, triage: bool = False, private: bool = False) -> dict[str, Any]:
    return {
        "id": n,
        "node_id": "MDEwOlJlcG9zaXRvcnkxMjk2MjY5",
        "name": f"repo-{n}",
        "full_name": f"octo-org/repo-{n}",
        "private": private,
        "default_branch": "main",
        "permissions": {"admin": admin, "maintain": maintain, "push": push, "triage": triage, "pull": True},
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
    catalog = make_catalog(router([installation(7), installation(8)], {7: [repo_json(1, admin=True)], 8: [repo_json(2, maintain=True), repo_json(3), repo_json(4, push=True), repo_json(5, triage=True)]}))

    assert catalog.list_repositories(TOKEN) == [
        InstallationRepository("1", "octo-org/repo-1", "main", RepositoryPermissions(admin=True, maintain=False, push=False, triage=False)),
        InstallationRepository("2", "octo-org/repo-2", "main", RepositoryPermissions(admin=False, maintain=True, push=False, triage=False)),
        InstallationRepository("3", "octo-org/repo-3", "main", RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)),
        InstallationRepository("4", "octo-org/repo-4", "main", RepositoryPermissions(admin=False, maintain=False, push=True, triage=False)),
        InstallationRepository("5", "octo-org/repo-5", "main", RepositoryPermissions(admin=False, maintain=False, push=False, triage=True)),
    ]


def test_pages_by_100_until_a_short_page() -> None:
    seen: list[str] = []
    installations = [installation(i) for i in range(1, 102)]
    repositories: dict[int, list[dict[str, Any]]] = {i: [] for i in range(1, 102)}
    repositories[1] = [repo_json(n) for n in range(250)]

    result = make_catalog(router(installations, repositories, seen)).list_repositories(TOKEN)

    assert len(result) == 250
    # Обход ленивый: страницы установок перемежаются с репозиториями.
    assert [s for s in seen if s.startswith("/user/installations?")] == [
        "/user/installations?per_page=100&page=1",
        "/user/installations?per_page=100&page=2",
    ]
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

    assert catalog.list_repositories(TOKEN)[0].permissions == RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)


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


def test_a_secondary_rate_limit_403_with_only_a_message_is_unavailable() -> None:
    """Вторичный лимит бывает `403` без `retry-after` и `x-ratelimit-remaining: 0`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/installations":
            return httpx.Response(200, json={"total_count": 1, "installations": [installation(7)]})
        return httpx.Response(403, json={"message": "You have exceeded a secondary Rate Limit. Please wait."})

    with pytest.raises(VcsUnavailableError):
        make_catalog(handler).list_repositories(TOKEN)


def test_skipped_installations_are_one_warning_without_the_token(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer ghu_user_token_value"
        if request.url.path == "/user/installations":
            body = [installation(6, suspended_at="2026-10-01T00:00:00Z"), installation(7), installation(8)]
            return httpx.Response(200, json={"total_count": 3, "installations": body})
        if request.url.path.startswith("/user/installations/7/"):
            return httpx.Response(403, json={"message": "Resource protected by organization SAML enforcement."})
        return httpx.Response(200, json={"total_count": 1, "repositories": [repo_json(2)]})

    with caplog.at_level(logging.INFO):
        make_catalog(handler).list_repositories(TOKEN)

    [warning] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "6" in warning.getMessage() and "7" in warning.getMessage()
    assert "ghu_user_token_value" not in caplog.text


# Подключение: два прямых запроса вместо обхода списка. Формы ответов — из
# документации GitHub REST: «Get a repository» (`GET /repositories/{id}` — тот же
# объект, что `GET /repos/{owner}/{repo}`, с `permissions` пользователя) и «Get a
# repository installation for the authenticated app» (`GET /repos/{owner}/{repo}/installation`,
# объект установки с `suspended_at`), https://docs.github.com/en/rest/apps/apps.


def test_user_repository_reads_one_repository_with_the_user_token() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        assert request.headers["Authorization"] == "Bearer ghu_user_token_value"
        return httpx.Response(200, json=repo_json(42, maintain=True))

    found = make_catalog(handler).user_repository(TOKEN, "42")

    assert found == InstallationRepository("42", "octo-org/repo-42", "main", RepositoryPermissions(admin=False, maintain=True, push=False, triage=False))
    assert seen == ["/repositories/42"]


def test_user_repository_reads_whether_the_repository_is_private() -> None:
    found = make_catalog(lambda request: httpx.Response(200, json=repo_json(42, private=True))).user_repository(TOKEN, "42")

    assert found is not None and found.private


@pytest.mark.parametrize("status", [403, 404])
def test_a_repository_the_user_cannot_see_is_none(status: int) -> None:
    assert make_catalog(lambda request: httpx.Response(status, json={"message": "Not Found"})).user_repository(TOKEN, "42") is None


@pytest.mark.parametrize("provider_id", ["../user", "²", "١٢٣"])
def test_a_non_numeric_id_is_none_without_asking_github(provider_id: str) -> None:
    # `str.isdigit()` принимает и не-ASCII цифры: в путь они не идут.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("запроса быть не должно")

    assert make_catalog(handler).user_repository(TOKEN, provider_id) is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"message": "Bad credentials"}),
        httpx.Response(200, json=repo_json(42) | {"permissions": None}),
        httpx.Response(403, json={"message": "API rate limit exceeded"}),
        httpx.Response(502),
    ],
)
def test_user_repository_failures_are_typed(response: httpx.Response) -> None:
    with pytest.raises((ProviderTokenRejected, VcsError)):
        make_catalog(lambda request: response).user_repository(TOKEN, "42")


def _installation_handler(response: httpx.Response, rsa_key_pair: RsaKeyPair, seen: list[str]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        # JWT приложения, подписанный его ключом: iss — App ID.
        claims = jwt.decode(request.headers["Authorization"].removeprefix("Bearer "), rsa_key_pair.public_pem, algorithms=["RS256"])
        assert claims["iss"] == "123456"
        return response

    return handler


def _app_catalog(response: httpx.Response, rsa_key_pair: RsaKeyPair, seen: list[str]) -> GitHubInstallationCatalog:
    handler = _installation_handler(response, rsa_key_pair, seen)
    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    return GitHubInstallationCatalog(client, GitHubAppAuth("123456", rsa_key_pair.private_pem, client))


def test_app_installed_asks_with_the_app_jwt(rsa_key_pair: RsaKeyPair) -> None:
    seen: list[str] = []
    catalog = _app_catalog(httpx.Response(200, json=installation(7)), rsa_key_pair, seen)

    assert catalog.app_installed("octo-org/api") is True
    assert seen == ["/repos/octo-org/api/installation"]


def test_not_installed_or_suspended_is_false(rsa_key_pair: RsaKeyPair) -> None:
    seen: list[str] = []
    assert _app_catalog(httpx.Response(404, json={"message": "Not Found"}), rsa_key_pair, seen).app_installed("o/r") is False
    suspended = httpx.Response(200, json=installation(7, suspended_at="2026-10-01T00:00:00Z"))
    assert _app_catalog(suspended, rsa_key_pair, seen).app_installed("o/r") is False


@pytest.mark.parametrize(
    "response",
    [httpx.Response(401), httpx.Response(403), httpx.Response(500), httpx.Response(200, json={"id": None}), httpx.Response(200, json=[])],
)
def test_app_installed_failures_are_vcs_errors(response: httpx.Response, rsa_key_pair: RsaKeyPair) -> None:
    with pytest.raises(VcsError):
        _app_catalog(response, rsa_key_pair, []).app_installed("o/r")


def test_repository_installation_id_asks_with_the_app_jwt_not_a_user_token(rsa_key_pair: RsaKeyPair) -> None:
    seen: list[str] = []
    catalog = _app_catalog(httpx.Response(200, json=installation(7)), rsa_key_pair, seen)

    # Обработчик декодирует заголовок как JWT приложения: токен пользователя не прошёл бы.
    assert catalog.repository_installation_id("octo-org/api") == 7
    assert seen == ["/repos/octo-org/api/installation"]


def test_repository_installation_id_is_none_when_not_installed_or_suspended(rsa_key_pair: RsaKeyPair) -> None:
    not_found = httpx.Response(404, json={"message": "Not Found"})
    assert _app_catalog(not_found, rsa_key_pair, []).repository_installation_id("o/r") is None
    suspended = httpx.Response(200, json=installation(7, suspended_at="2026-10-01T00:00:00Z"))
    assert _app_catalog(suspended, rsa_key_pair, []).repository_installation_id("o/r") is None


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(503),
        httpx.Response(429),
        httpx.Response(403, headers={"x-ratelimit-remaining": "0"}),
    ],
)
def test_repository_installation_id_outage_or_rate_limit_is_unavailable(response: httpx.Response, rsa_key_pair: RsaKeyPair) -> None:
    with pytest.raises(VcsUnavailableError):
        _app_catalog(response, rsa_key_pair, []).repository_installation_id("o/r")


def test_repository_installation_id_rejected_jwt_is_an_auth_error(rsa_key_pair: RsaKeyPair) -> None:
    with pytest.raises(VcsAuthError):
        _app_catalog(httpx.Response(401), rsa_key_pair, []).repository_installation_id("o/r")


def test_extra_github_fields_are_ignored() -> None:
    item = repo_json(1) | {"owner": {"login": "octo-org"}, "topics": ["x"], "unknown_future_field": {"a": 1}}
    item["permissions"] = item["permissions"] | {"new_role": True}
    catalog = make_catalog(router([installation(7) | {"html_url": "https://github.com/x"}], {7: [item]}))

    assert [r.provider_id for r in catalog.list_repositories(TOKEN)] == ["1"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda item: item.update(default_branch=None),
        lambda item: item.update(id=True),
        lambda item: item.update(id="1"),
        lambda item: item["permissions"].update(push=None),
    ],
)
def test_strict_types_reject_and_the_message_carries_no_values(mutate: Callable[[dict[str, Any]], None]) -> None:
    item = repo_json(1) | {"full_name": "secret-org/secret-name"}
    item["permissions"] = dict(item["permissions"])
    mutate(item)

    with pytest.raises(VcsError) as raised:
        make_catalog(router([installation(7)], {7: [item]})).list_repositories(TOKEN)

    message = str(raised.value)
    assert "repositories.0." in message
    assert "secret-org" not in message and "secret-name" not in message


def test_a_bool_installation_id_is_rejected() -> None:
    body = {"total_count": 1, "installations": [installation(7) | {"id": True}]}
    with pytest.raises(VcsError):
        make_catalog(lambda request: httpx.Response(200, json=body)).list_repositories(TOKEN)


def test_a_malformed_suspended_at_is_rejected(rsa_key_pair: RsaKeyPair) -> None:
    response = httpx.Response(200, json=installation(7, suspended_at="вчера"))
    with pytest.raises(VcsError):
        _app_catalog(response, rsa_key_pair, []).app_installed("o/r")


# --- Setup URL: установка пользователя и её репозитории ------------------------------


def test_installation_selection_finds_the_installation_among_the_users_own() -> None:
    installations = [installation(i) for i in range(1, 101)] + [installation(150, selection="all")]
    catalog = make_catalog(router(installations, {}))

    assert catalog.installation_selection(TOKEN, 3) is RepositorySelection.SELECTED
    assert catalog.installation_selection(TOKEN, 150) is RepositorySelection.ALL
    # Чужой id из адреса Setup URL — не среди установок пользователя.
    assert catalog.installation_selection(TOKEN, 999) is None


def test_a_suspended_installation_has_no_selection() -> None:
    catalog = make_catalog(router([installation(7, suspended_at="2026-10-01T00:00:00Z")], {}))

    assert catalog.installation_selection(TOKEN, 7) is None


def test_an_unknown_or_missing_selection_is_a_vcs_error() -> None:
    unknown = make_catalog(router([installation(7, selection="some")], {}))
    without = installation(7)
    del without["repository_selection"]
    missing = make_catalog(router([without], {}))

    with pytest.raises(VcsError):
        unknown.installation_selection(TOKEN, 7)
    with pytest.raises(VcsError):
        missing.installation_selection(TOKEN, 7)


def test_installation_repositories_reads_every_page_of_one_installation() -> None:
    seen: list[str] = []
    catalog = make_catalog(router([installation(7)], {7: [repo_json(n, admin=n == 1) for n in range(1, 151)]}, seen))

    repositories = catalog.installation_repositories(TOKEN, 7)

    assert len(repositories) == 150
    assert repositories[0].permissions.admin is True
    assert seen == [
        "/user/installations/7/repositories?per_page=100&page=1",
        "/user/installations/7/repositories?per_page=100&page=2",
    ]


@pytest.mark.parametrize("status", [403, 404])
def test_installation_repositories_closed_for_the_token_is_a_vcs_error(status: int) -> None:
    """Установку только что нашли: пустой список вместо сбоя скрыл бы его."""
    catalog = make_catalog(lambda request: httpx.Response(status, json={"message": "Not Found"}))

    with pytest.raises(VcsError, match=f"HTTP {status}"):
        catalog.installation_repositories(TOKEN, 7)


def test_a_forbidden_installation_says_access_is_denied_not_that_github_is_down() -> None:
    """`403` (SSO не авторизован) виден в тексте ошибки: в логе Setup URL — не «GitHub не ответил»."""
    catalog = make_catalog(lambda request: httpx.Response(403, json={"message": "Resource protected by SAML"}))

    with pytest.raises(VcsError, match="HTTP 403 .*SSO"):
        catalog.installation_repositories(TOKEN, 7)


def test_an_unknown_selection_does_not_break_the_repository_list() -> None:
    """Значение `repository_selection` нужно только Setup URL: незнакомое не роняет список."""
    catalog = make_catalog(router([installation(7, selection="future")], {7: [repo_json(1, admin=True)]}))

    assert [r.provider_id for r in catalog.list_repositories(TOKEN)] == ["1"]
