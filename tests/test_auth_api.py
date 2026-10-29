"""Контракт входа, сессии и проверки `Origin`. Без БД: фейковые unit of work и провайдер."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
    get_app_slug,
    get_clock,
    get_installation_catalog,
    get_oauth_provider,
    get_run_changes,
    get_token_provider,
    get_unit_of_work_factory,
    get_untraced_unit_of_work_factory,
)
from app.api.factory import create_app
from app.application.use_cases.auth import logout
from app.config import Settings
from app.domain.entities import (
    InstallationRepository,
    Repository,
    RepositoryPermissions,
)
from app.domain.enums import Provider, RepositorySelection
from app.domain.ids import new_id
from app.domain.login import OAuthProviderUnavailable
from app.domain.provider_token import ProviderTokenRejected
from app.domain.session import setup_state
from app.domain.vcs_errors import VcsError
from app.infrastructure.oauth.github import GitHubOAuthProvider

from .fakes.installation_catalog import FakeInstallationCatalog
from .fakes.oauth import FakeOAuthProvider
from .fakes.run_changes import FakeRunChanges, every_step
from .fakes.unit_of_work import FakeUnitOfWork

ORIGIN = "https://review.example"
SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
    public_origin=ORIGIN,
)


class Stand:
    def __init__(self) -> None:
        self.uow = FakeUnitOfWork()
        self.provider = FakeOAuthProvider()
        self.app = create_app(SETTINGS)
        self.app.dependency_overrides[get_unit_of_work_factory] = lambda: lambda: self.uow
        self.app.dependency_overrides[get_oauth_provider] = self._provider
        self.app.dependency_overrides[get_token_provider] = lambda: self.provider

    def _provider(self, provider: str) -> FakeOAuthProvider | None:
        return self.provider if provider == "github" else None

    def client(self) -> TestClient:
        # https: cookie с `Secure` клиент по http обратно не отправит.
        return TestClient(self.app, base_url=ORIGIN, follow_redirects=False)


@pytest.fixture
def stand() -> Stand:
    return Stand()


def set_cookies(response) -> dict[str, str]:
    """Имя cookie → строка `Set-Cookie` целиком."""
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


def sign_in(client: TestClient, redirect: str | None = "/app/reviews/42", **callback):
    params = {"redirect": redirect} if redirect is not None else {}
    start = client.get("/api/v1/auth/github/login", params=params)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    return client.get(
        "/api/v1/auth/github/callback", params={"code": "code-1", "state": state} | callback
    )


def test_login_redirects_to_the_provider_and_remembers_the_state(stand) -> None:
    response = stand.client().get("/api/v1/auth/github/login")

    assert response.status_code == 302
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"]
    cookie = set_cookies(response)["oauth_state"]
    assert cookie.startswith(f"oauth_state={state[0]}|")
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/api/v1/auth", "Max-Age=600"):
        assert attribute in cookie


def test_an_unknown_provider_is_404(stand) -> None:
    client = stand.client()

    for path in ("/api/v1/auth/bitbucket/login", "/api/v1/auth/bitbucket/callback"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["code"] == "PROVIDER_NOT_SUPPORTED"
        assert body["detail"] == "Вход через этого провайдера не поддержан."
        assert body["instance"] == path


def test_callback_sets_the_session_and_returns_to_the_deep_link(stand) -> None:
    client = stand.client()

    response = sign_in(client)

    assert (response.status_code, response.headers["location"]) == (302, "/app/reviews/42")
    cookies = set_cookies(response)
    for attribute in ("HttpOnly", "Secure", "SameSite=lax", "Path=/", "Max-Age=2592000"):
        assert attribute in cookies["session"]
    assert 'oauth_state=""' in cookies["oauth_state"] and "Max-Age=0" in cookies["oauth_state"]
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "provider": "github",
        "login": "octocat",
        "name": "The Octocat",
        "avatar_url": "https://avatars.githubusercontent.com/u/583231",
    }


@pytest.mark.parametrize("redirect", [None, "https://evil.example/", "//evil.example/app/"])
def test_login_without_a_safe_redirect_lands_on_app(stand, redirect) -> None:
    response = sign_in(stand.client(), redirect=redirect)

    assert response.headers["location"] == "/app/"


@pytest.mark.parametrize(
    ("callback", "error"),
    [
        ({"code": "code-1", "state": "forged"}, "state_mismatch"),
        ({"error": "access_denied"}, "denied"),
    ],
)
def test_a_failed_callback_goes_to_login_without_a_session(stand, callback, error) -> None:
    client = stand.client()
    start = client.get("/api/v1/auth/github/login")
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]

    response = client.get("/api/v1/auth/github/callback", params={"state": state} | callback)

    assert response.headers["location"] == f"/login?error={error}"
    assert "session" not in set_cookies(response)
    assert stand.uow.sessions.stored == []


def test_a_callback_without_the_state_cookie_is_a_mismatch(stand) -> None:
    response = stand.client().get(
        "/api/v1/auth/github/callback", params={"code": "c", "state": "s"}
    )

    assert response.headers["location"] == "/login?error=state_mismatch"


def test_a_non_ascii_state_is_a_mismatch_and_clears_the_state_cookie(stand) -> None:
    client = stand.client()
    client.cookies.set("oauth_state", "state-1|%2Fapp%2F", domain="review.example")

    response = client.get("/api/v1/auth/github/callback", params={"code": "c", "state": "é"})

    assert (response.status_code, response.headers["location"]) == (
        302,
        "/login?error=state_mismatch",
    )
    cookie = set_cookies(response)["oauth_state"]
    assert 'oauth_state=""' in cookie and "Max-Age=0" in cookie


@pytest.mark.parametrize(
    ("client_id", "callback_url", "reason"),
    [
        ("", f"{ORIGIN}/api/v1/auth/github/callback", "GITHUB_APP_CLIENT_ID"),
        ("Iv1.client", "/api/v1/auth/github/callback", "PUBLIC_ORIGIN"),
    ],
)
def test_login_without_a_configured_provider_goes_to_login(
    stand, caplog, monkeypatch, client_id, callback_url, reason
) -> None:
    stand.provider = GitHubOAuthProvider(
        client_id, "client-secret", callback_url, httpx.Client()
    )
    caplog.set_level(logging.WARNING)
    # `fileConfig` в тестах Alembic отключает уже созданные логгеры.
    # TODO: убрать после мёржа #65 (change fix-migration-logging), как и такой же
    # обход в tests/worker/test_review_handler.py.
    monkeypatch.setattr(logging.getLogger("app.application.use_cases.auth"), "disabled", False)

    response = stand.client().get("/api/v1/auth/github/login")

    assert (response.status_code, response.headers["location"]) == (
        302,
        "/login?error=provider_unavailable",
    )
    assert "oauth_state" not in set_cookies(response)
    assert reason in caplog.text
    assert "client-secret" not in caplog.text


def test_an_unavailable_provider_goes_to_login(stand) -> None:
    stand.provider.unavailable = True

    response = sign_in(stand.client())

    assert response.headers["location"] == "/login?error=provider_unavailable"
    assert stand.uow.sessions.stored == []


@pytest.mark.parametrize(
    "path",
    ["/api/v1/auth/me", "/api/v1/repositories", f"/api/v1/reviews/{new_id()}",
     f"/api/v1/repositories/{new_id()}/pull-requests", "/api/v1/events"],
)
def test_reading_without_a_session_is_401_and_sets_no_cookie(stand, path) -> None:
    response = stand.client().get(path)

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"
    assert "set-cookie" not in response.headers


def test_an_unknown_session_cookie_is_401_and_deleted(stand) -> None:
    client = stand.client()
    client.cookies.set("session", "never-issued", domain="review.example")

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    cookie = set_cookies(response)["session"]
    assert "Max-Age=0" in cookie and "Path=/" in cookie


def test_an_expired_session_is_401_and_deleted(stand) -> None:
    client = stand.client()
    sign_in(client)
    [session] = stand.uow.sessions.stored
    stand.uow.sessions.add(replace(session, expires_at=datetime(2026, 1, 1, tzinfo=UTC)))

    response = client.get("/api/v1/auth/me")

    assert response.status_code == 401
    assert "Max-Age=0" in set_cookies(response)["session"]
    assert stand.uow.sessions.stored == []


@pytest.mark.parametrize("path", ["/api/v1/auth/me", "/api/v1/repositories"])
def test_a_session_without_a_provider_token_is_401_and_deleted_everywhere(stand, path) -> None:
    """Раньше `/auth/me` отвечал такой сессии 200, а поток `/events` — 401 (#120)."""
    client = stand.client()
    sign_in(client)
    stand.uow.provider_tokens.restore({})

    response = client.get(path)

    assert response.status_code == 401
    assert "Max-Age=0" in set_cookies(response)["session"]
    assert stand.uow.sessions.stored == []


# --- Setup URL: возврат с установки GitHub App (#243, design D7) --------------------

ADMIN = RepositoryPermissions(admin=True, maintain=False, push=False, triage=False)
WRITE = RepositoryPermissions(admin=False, maintain=False, push=True, triage=False)
OWNED = InstallationRepository("101", "octo-org/api", "main", ADMIN)
WRITTEN = InstallationRepository("202", "octo-org/web", "main", WRITE)
SETUP = "/api/v1/auth/github/setup"


def _setup_stand(stand: Stand, installations: dict[int, tuple[RepositorySelection, list[InstallationRepository]]], *, error: Exception | None = None) -> tuple[TestClient, FakeInstallationCatalog]:
    """Вошедший пользователь; GitHub отдаёт ему эти установки и все их репозитории."""
    visible = [r for _, repositories in installations.values() for r in repositories]
    catalog = FakeInstallationCatalog(visible, error, installations=installations)
    stand.app.dependency_overrides[get_installation_catalog] = lambda: catalog
    client = stand.client()
    sign_in(client)
    return client, catalog


def _return_from_github(client: TestClient, installation_id: int = 7, *, state: str | None = "own", action: str = "install"):
    params: dict[str, str | int] = {"installation_id": installation_id, "setup_action": action}
    if state == "own":
        params["state"] = setup_state(client.cookies["session"])
    elif state is not None:
        params["state"] = state
    return client.get(SETUP, params=params)


def _registered(stand: Stand) -> dict[str, Repository]:
    return {provider_id: r for (_, provider_id), r in stand.uow.repositories.stored.items()}


def test_selected_repositories_with_manage_rights_are_connected(stand) -> None:
    client, _ = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [OWNED, WRITTEN])})

    response = _return_from_github(client)

    assert (response.status_code, response.headers["location"]) == (302, "/app/repositories?connected=1")
    registered = _registered(stand)
    assert list(registered) == ["101"]
    assert registered["101"].connected_at is not None and registered["101"].auto_review_enabled


def test_a_disconnected_repository_is_not_reconnected_by_the_setup_return(stand) -> None:
    """Выключение — решение пользователя; `update` приходит и при правке чужого списка установки."""
    client, _ = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [OWNED])})
    disconnected = Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id="101",
        full_name="octo-org/api",
        default_branch="main",
        auto_review_enabled=True,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
        connected_at=None,
    )
    stand.uow.repositories.add(disconnected)

    response = _return_from_github(client)

    assert response.headers["location"] == "/app/repositories?connected=0"
    assert _registered(stand) == {"101": disconnected}


def test_an_update_of_the_installation_connects_nothing_but_refreshes_the_cache(stand) -> None:
    """В установке A, B и C, ревью включено только у A: B и C оставлены выключенными намеренно.
    Пользователь дописал в установку D и вернулся с `update` — ничего не включается."""
    a, b, c, d = (InstallationRepository(str(n), f"octo-org/r{n}", "main", ADMIN) for n in (1, 2, 3, 4))
    client, catalog = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [a, b, c, d])})
    enabled = Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id="1",
        full_name="octo-org/r1",
        default_branch="main",
        auto_review_enabled=True,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        updated_at=datetime(2026, 9, 1, tzinfo=UTC),
        connected_at=datetime(2026, 9, 1, tzinfo=UTC),
    )
    stand.uow.repositories.add(enabled)

    response = _return_from_github(client, action="update")

    assert response.headers["location"] == "/app/repositories?connected=0"
    assert _registered(stand) == {"1": enabled}
    assert "list_repositories" in catalog.calls
    assert "installation_repositories" not in catalog.calls


def test_an_update_without_state_refreshes_the_cache_and_does_not_warn(stand, caplog) -> None:
    """«Redirect on update»: GitHub возвращает после правки установки, `state` может не быть.
    `update` ничего не подключает, поэтому `state` ему не нужен — кэш обновляется без warning."""
    client, catalog = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [OWNED])})

    with caplog.at_level(logging.INFO):
        response = _return_from_github(client, state=None, action="update")

    assert response.headers["location"] == "/app/repositories?connected=0"
    assert _registered(stand) == {}
    assert "list_repositories" in catalog.calls
    assert not [r for r in caplog.records if r.name == "app.api.auth" and r.levelno >= logging.WARNING]


def test_a_failed_cache_refresh_does_not_stop_the_connection(stand, caplog) -> None:
    """Обход всех установок нужен только кэшу: его сбой не мешает подключить по нужной установке."""
    client, catalog = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [OWNED])})

    def other_installation_fails(access_token):
        raise VcsError("GitHub API: HTTP 503 на GET /user/installations/9/repositories")

    catalog.list_repositories = other_installation_fails  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING):
        response = _return_from_github(client)

    assert response.headers["location"] == "/app/repositories?connected=1"
    assert list(_registered(stand)) == ["101"]
    assert "кэш видимости не обновлён" in caplog.text


def test_all_repositories_connect_nothing(stand) -> None:
    client, catalog = _setup_stand(stand, {7: (RepositorySelection.ALL, [OWNED])})

    response = _return_from_github(client)

    assert response.headers["location"] == "/app/repositories?connected=0"
    assert _registered(stand) == {}
    assert "installation_repositories" not in catalog.calls


def test_a_request_to_the_owner_connects_nothing_and_asks_github_nothing(stand) -> None:
    client, catalog = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [OWNED])})

    response = client.get(SETUP, params={"setup_action": "request"})

    assert response.headers["location"] == "/app/repositories?requested=true"
    assert (_registered(stand), catalog.calls) == ({}, [])


@pytest.mark.parametrize("params", [{"installation_id": "8", "setup_action": "install"}, {"setup_action": "update"}])
def test_a_foreign_or_missing_installation_is_not_found(stand, params) -> None:
    """`installation_id` из адреса не доверяется: установки 8 у пользователя нет."""
    client, _ = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [OWNED])})

    response = client.get(SETUP, params=params | {"state": setup_state(client.cookies["session"])})

    assert response.headers["location"] == "/app/repositories?setup_error=installation_not_found"
    assert _registered(stand) == {}


def test_github_unavailable_is_reported_and_connects_nothing(stand, caplog) -> None:
    """Статус GitHub попадает в лог: `403` (SSO) отличим от недоступного GitHub."""
    client, _ = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [OWNED])}, error=VcsError("GitHub API: HTTP 403"))

    with caplog.at_level(logging.WARNING):
        response = _return_from_github(client)

    assert response.headers["location"] == "/app/repositories?setup_error=provider_unavailable"
    assert _registered(stand) == {}
    assert "HTTP 403" in caplog.text


def test_a_rejected_token_closes_the_session_and_goes_to_login(stand) -> None:
    client, _ = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [OWNED])}, error=ProviderTokenRejected("401"))
    stand.provider.refresh_error = ProviderTokenRejected("bad_refresh_token")

    response = _return_from_github(client)

    assert response.headers["location"] == "/login?redirect=/app/repositories"
    assert "Max-Age=0" in set_cookies(response)["session"]
    assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("state", [None, "forged", "é" * 10])
def test_without_the_state_of_this_session_nothing_is_connected(stand, caplog, state) -> None:
    """Подделанный переход с чужого сайта: `state` этой сессии он не знает — к GitHub не идём.
    В логе — что `state` не совпал (проверка на стенде), но не его значение."""
    client, catalog = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [OWNED])})

    with caplog.at_level(logging.INFO):
        response = _return_from_github(client, state=state)

    assert response.headers["location"] == "/app/repositories?connected=0"
    assert (_registered(stand), catalog.calls) == ({}, [])
    # Только записи сервиса: тестовый клиент httpx сам пишет адрес запроса в лог.
    logged = " ".join(r.getMessage() for r in caplog.records if r.name == "app.api.auth")
    assert "state не совпал" in logged
    if state is not None:
        assert state not in logged


def test_the_state_of_another_session_is_refused(stand) -> None:
    client, catalog = _setup_stand(stand, {7: (RepositorySelection.SELECTED, [OWNED])})

    response = _return_from_github(client, state=setup_state("cookie-of-someone-else"))

    assert response.headers["location"] == "/app/repositories?connected=0"
    assert catalog.calls == []


def test_the_install_url_state_is_accepted_and_the_new_repository_is_listed_at_once(stand) -> None:
    """Сквозной путь: `state` из `install_url` списка; свежий ответ GitHub — в кэш, без ожидания TTL."""
    current = {"catalog": FakeInstallationCatalog([WRITTEN])}
    stand.app.dependency_overrides[get_installation_catalog] = lambda: current["catalog"]
    stand.app.dependency_overrides[get_app_slug] = lambda: lambda: "dmc-268-review"
    client = stand.client()
    sign_in(client)
    before = client.get("/api/v1/repositories").json()
    state = parse_qs(urlsplit(before["install_url"]).query)["state"][0]
    # Пользователь поставил приложение на `octo-org/api`; кэш видимости ещё помнит старый ответ.
    current["catalog"] = FakeInstallationCatalog(
        [WRITTEN, OWNED], installations={7: (RepositorySelection.SELECTED, [OWNED])}
    )

    response = client.get(SETUP, params={"installation_id": 7, "setup_action": "install", "state": state})
    after = client.get("/api/v1/repositories").json()["items"]

    assert [row["full_name"] for row in before["items"]] == ["octo-org/web"]
    assert response.headers["location"] == "/app/repositories?connected=1"
    assert [(row["full_name"], row["connected"]) for row in after] == [("octo-org/api", True), ("octo-org/web", False)]
    assert current["catalog"].calls.count("list_repositories") == 1


def test_the_setup_return_without_a_session_goes_to_login_and_back(stand) -> None:
    """Браузер пришёл с GitHub: вместо JSON `401` — вход с возвратом на «Репозитории»."""
    response = stand.client().get(
        "/api/v1/auth/github/setup", params={"installation_id": "42", "setup_action": "install"}
    )

    assert (response.status_code, response.headers["location"]) == (302, "/login?redirect=/app/repositories")
    assert "set-cookie" not in response.headers


def test_the_setup_return_with_a_dead_session_clears_the_cookie(stand) -> None:
    client = stand.client()
    client.cookies.set("session", "never-issued", domain="review.example")

    response = client.get("/api/v1/auth/github/setup", params={"setup_action": "request"})

    assert (response.status_code, response.headers["location"]) == (302, "/login?redirect=/app/repositories")
    assert "Max-Age=0" in set_cookies(response)["session"]


@pytest.mark.parametrize(
    ("params", "where"),
    [
        ({"installation_id": "42"}, "query.setup_action"),
        ({"installation_id": "42", "setup_action": "uninstall"}, "query.setup_action"),
        ({"installation_id": "0", "setup_action": "install"}, "query.installation_id"),
        ({"installation_id": "abc", "setup_action": "install"}, "query.installation_id"),
    ],
)
def test_the_setup_return_checks_its_parameters_first(stand, params, where) -> None:
    client = stand.client()
    sign_in(client)

    response = client.get("/api/v1/auth/github/setup", params=params)

    assert response.status_code == 422
    assert [error["where"] for error in response.json()["errors"]] == [where]


def test_logout_ends_the_session_in_every_tab(stand) -> None:
    tab_1 = stand.client()
    sign_in(tab_1)
    tab_2 = stand.client()
    tab_2.cookies.set("session", tab_1.cookies["session"], domain="review.example")

    response = tab_1.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})

    assert response.status_code == 204
    assert "Max-Age=0" in set_cookies(response)["session"]
    assert tab_2.get("/api/v1/auth/me").status_code == 401


def test_logging_in_again_revokes_the_session_of_the_cookie_it_came_with(stand) -> None:
    client = stand.client()
    sign_in(client)
    first = client.cookies["session"]

    sign_in(client)

    old_tab = stand.client()
    old_tab.cookies.set("session", first, domain="review.example")
    assert old_tab.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 200
    assert len(stand.uow.sessions.stored) == 1


def test_logout_without_a_session_is_204_and_clears_the_cookie(stand) -> None:
    response = stand.client().post("/api/v1/auth/logout", headers={"Origin": ORIGIN})

    assert response.status_code == 204
    assert "Max-Age=0" in set_cookies(response)["session"]


def test_logout_with_an_unknown_session_is_204_and_clears_the_cookie(stand) -> None:
    client = stand.client()
    client.cookies.set("session", "never-issued", domain="review.example")

    response = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})

    assert response.status_code == 204
    assert "Max-Age=0" in set_cookies(response)["session"]


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}, {"Origin": "null"}])
def test_a_mutating_request_from_elsewhere_is_403_and_changes_nothing(stand, headers) -> None:
    client = stand.client()
    sign_in(client)

    response = client.post("/api/v1/auth/logout", headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_FORBIDDEN"
    assert len(stand.uow.sessions.stored) == 1


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}])
def test_a_mutating_request_from_elsewhere_without_a_session_is_403(stand, headers) -> None:
    # `Origin` проверяется раньше сессии: не `401` и не идемпотентный `204` выхода.
    response = stand.client().post("/api/v1/auth/logout", headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_FORBIDDEN"
    assert "set-cookie" not in response.headers


def test_webhooks_need_neither_a_session_nor_an_origin(stand) -> None:
    response = stand.client().post("/api/v1/webhooks/github", content=b"{}")

    assert response.json()["code"] == "WEBHOOK_SIGNATURE_INVALID"


def test_neither_the_session_cookie_nor_the_code_reaches_the_app_logs(stand, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    client = stand.client()

    responses = [sign_in(client, code="code-secret-1")]
    token = client.cookies["session"]
    responses.append(client.get("/api/v1/auth/me"))
    responses.append(client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN}))

    # Запросы пишет сам тестовый клиент — это не логи приложения.
    app_logs = "\n".join(r.getMessage() for r in caplog.records if r.name not in ("httpx", "httpx2"))
    assert "code-secret-1" not in app_logs
    assert token not in app_logs
    assert "set-cookie" not in app_logs.lower()
    # Токен пользователя GitHub сохранён при сессии, но не уходит ни в лог, ни в браузер.
    for secret in ("ghu_login_access", "ghr_login_refresh"):
        assert secret not in app_logs
        for response in responses:
            assert secret not in response.text
            assert secret not in str(response.headers)


def test_login_keeps_the_provider_token_and_logout_drops_it(stand) -> None:
    client = stand.client()

    sign_in(client)
    [session] = stand.uow.sessions.stored
    assert stand.uow.provider_tokens.get(session.id) is not None
    client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})

    assert stand.uow.provider_tokens.stored == {}
    # Отзыв у GitHub — после ответа, тем токеном, что был у сессии.
    assert [t.reveal() for t in stand.provider.revoked] == ["ghu_login_access"]


def test_a_failed_revoke_does_not_spoil_logout(stand, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    stand.provider.revoke_error = OAuthProviderUnavailable("GitHub ответил 503 (отзыв токена)")
    client = stand.client()
    sign_in(client)

    response = client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})

    assert response.status_code == 204
    assert stand.uow.sessions.stored == []
    assert "ghu_login_access" not in caplog.text


def test_logout_without_a_session_revokes_nothing(stand) -> None:
    stand.client().post("/api/v1/auth/logout", headers={"Origin": ORIGIN})
    assert stand.provider.revoked == []


def test_the_access_log_hides_the_callback_query() -> None:
    create_app(SETTINGS)
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 0, '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/v1/auth/github/callback?code=c-1&state=s-1", "1.1", 302),
        None,
    )

    assert logging.getLogger("uvicorn.access").filter(record)

    message = record.getMessage()
    assert "c-1" not in message and "s-1" not in message
    assert "/api/v1/auth/github/callback?<скрыто>" in message


def test_logout_closes_an_open_event_stream_and_reconnecting_is_401(stand) -> None:
    slept: list[float] = []
    now = [0.0]
    client = stand.client()
    sign_in(client)
    token = client.cookies["session"]

    async def sleep_then_logout(seconds: float) -> None:
        slept.append(seconds)
        now[0] += seconds
        assert len(slept) < 30, "поток не закрылся после выхода"
        if len(slept) == 2:
            # Выход в другой вкладке, пока этот поток открыт.
            logout(stand.uow, token)

    stand.app.dependency_overrides[get_untraced_unit_of_work_factory] = lambda: lambda: stand.uow
    stand.app.dependency_overrides[get_installation_catalog] = lambda: FakeInstallationCatalog()
    stand.app.dependency_overrides[get_run_changes] = lambda: FakeRunChanges(every_step(sleep_then_logout))
    stand.app.dependency_overrides[get_clock] = lambda: lambda: now[0]

    response = client.get("/api/v1/events")

    assert response.status_code == 200
    assert response.text == "retry: 3000\n\n"
    assert sum(slept) <= 5
    assert client.get("/api/v1/events").status_code == 401
