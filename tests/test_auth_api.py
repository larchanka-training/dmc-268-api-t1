"""Контракт входа, сессии и проверки `Origin`. Без БД: фейковые unit of work и провайдер."""

import logging
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from app.api.dependencies import get_oauth_provider, get_unit_of_work_factory
from app.api.factory import create_app
from app.config import Settings
from app.domain.ids import new_id

from .fakes.oauth import FakeOAuthProvider
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

    assert client.get("/api/v1/auth/bitbucket/login").status_code == 404
    assert client.get("/api/v1/auth/bitbucket/callback").status_code == 404


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


def test_an_unavailable_provider_goes_to_login(stand) -> None:
    stand.provider.unavailable = True

    response = sign_in(stand.client())

    assert response.headers["location"] == "/login?error=provider_unavailable"
    assert stand.uow.sessions.stored == []


@pytest.mark.parametrize(
    "path",
    ["/api/v1/auth/me", "/api/v1/repositories", f"/api/v1/reviews/{new_id()}",
     f"/api/v1/repositories/{new_id()}/pull-requests"],
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


def test_logout_ends_the_session_in_every_tab(stand) -> None:
    tab_1 = stand.client()
    sign_in(tab_1)
    tab_2 = stand.client()
    tab_2.cookies.set("session", tab_1.cookies["session"], domain="review.example")

    response = tab_1.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})

    assert response.status_code == 204
    assert "Max-Age=0" in set_cookies(response)["session"]
    assert tab_2.get("/api/v1/auth/me").status_code == 401


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}, {"Origin": "null"}])
def test_a_mutating_request_from_elsewhere_is_403_and_changes_nothing(stand, headers) -> None:
    client = stand.client()
    sign_in(client)

    response = client.post("/api/v1/auth/logout", headers=headers)

    assert response.status_code == 403
    assert response.json()["code"] == "ORIGIN_FORBIDDEN"
    assert len(stand.uow.sessions.stored) == 1


def test_webhooks_need_neither_a_session_nor_an_origin(stand) -> None:
    response = stand.client().post("/api/v1/webhooks/github", content=b"{}")

    assert response.json()["code"] == "WEBHOOK_SIGNATURE_INVALID"


def test_neither_the_session_cookie_nor_the_code_reaches_the_app_logs(stand, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    client = stand.client()

    sign_in(client, code="code-secret-1")
    token = client.cookies["session"]
    client.get("/api/v1/auth/me")
    client.post("/api/v1/auth/logout", headers={"Origin": ORIGIN})

    # Запросы пишет сам тестовый клиент — это не логи приложения.
    app_logs = "\n".join(r.getMessage() for r in caplog.records if r.name != "httpx")
    assert "code-secret-1" not in app_logs
    assert token not in app_logs
    assert "set-cookie" not in app_logs.lower()


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
