"""Контракт входа, сессии и проверки `Origin`. Без БД: фейковые unit of work и провайдер."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import (
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
from app.domain.ids import new_id
from app.domain.login import OAuthProviderUnavailable
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


# --- Setup URL: контракт первым, реализация — следующим шагом #243 ----------------


@pytest.mark.parametrize(
    "params",
    [{"installation_id": "42", "setup_action": "install"}, {"setup_action": "request"}],
)
def test_the_setup_return_is_planned_and_answers_501(stand, params) -> None:
    client = stand.client()
    sign_in(client)

    response = client.get("/api/v1/auth/github/setup", params=params)

    assert response.status_code == 501
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "NOT_IMPLEMENTED"


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
def test_the_setup_return_checks_its_parameters_before_501(stand, params, where) -> None:
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
