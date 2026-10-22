"""`GitHubOAuthProvider` на записанных ответах: обмен `code`, `GET /user` и refresh.

Формы ответов `POST https://github.com/login/oauth/access_token` — из документации
GitHub Apps: «Generating a user access token for a GitHub App» (с истечением —
`access_token`, `expires_in`, `refresh_token`, `refresh_token_expires_in`,
`scope`, `token_type`; без истечения — только `access_token`, `scope`,
`token_type`) и «Refreshing user access tokens» (`grant_type=refresh_token`,
ошибка — `200` с `{"error": "bad_refresh_token"}`).
"""

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.domain.entities import OAuthProfile, ProviderGrant, Secret
from app.domain.login import OAuthProviderUnavailable
from app.domain.provider_token import ProviderTokenRejected
from app.infrastructure.oauth.github import GitHubOAuthProvider

CALLBACK = "https://review.example/api/v1/auth/github/callback"
USER_TOKEN = "ghu_user-token-value"
USER_JSON: dict[str, Any] = {
    "id": 583231,
    "login": "octocat",
    "name": "The Octocat",
    "avatar_url": "https://avatars.githubusercontent.com/u/583231",
}

Handler = Callable[[httpx.Request], httpx.Response]


def provider(handler: Handler) -> GitHubOAuthProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GitHubOAuthProvider("Iv1.client", "client-secret", CALLBACK, client)


def github(token_response: httpx.Response, user_response: httpx.Response) -> Handler:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url == "https://github.com/login/oauth/access_token":
            return token_response
        if request.url == "https://api.github.com/user":
            assert request.headers["Authorization"] == f"Bearer {USER_TOKEN}"
            return user_response
        raise AssertionError(f"неожиданный запрос {request.url}")

    return handle


def ok_token() -> httpx.Response:
    return httpx.Response(200, json={"access_token": USER_TOKEN, "token_type": "bearer"})


def test_authorize_url_carries_the_client_state_and_callback() -> None:
    url = urlsplit(provider(github(ok_token(), httpx.Response(200))).authorize_url("s-1"))

    assert (url.scheme, url.netloc, url.path) == ("https", "github.com", "/login/oauth/authorize")
    assert parse_qs(url.query) == {
        "client_id": ["Iv1.client"],
        "state": ["s-1"],
        "redirect_uri": [CALLBACK],
    }


def test_the_code_is_exchanged_with_the_secret_and_the_profile_returned() -> None:
    seen: list[httpx.Request] = []
    handle = github(ok_token(), httpx.Response(200, json=USER_JSON))

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handle(request)

    login = provider(record).exchange_code("code-1")

    assert login.profile == OAuthProfile("583231", "octocat", "The Octocat", USER_JSON["avatar_url"])
    # Ответ без истечения: только access token, без сроков и refresh token.
    assert login.grant == ProviderGrant(Secret(USER_TOKEN), None, None, None)
    exchange = seen[0]
    assert exchange.method == "POST"
    assert exchange.headers["Accept"] == "application/json"
    assert parse_qs(exchange.content.decode()) == {
        "client_id": ["Iv1.client"],
        "client_secret": ["client-secret"],
        "code": ["code-1"],
        "redirect_uri": [CALLBACK],
    }


def test_a_profile_without_a_name_is_fine() -> None:
    user = USER_JSON | {"name": None}

    login = provider(github(ok_token(), httpx.Response(200, json=user))).exchange_code("c")

    assert login.profile.name is None


def _transport_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("нет сети", request=request)


@pytest.mark.parametrize(
    "handler",
    [
        # GitHub отвечает на неверный code статусом 200 и полем error.
        github(httpx.Response(200, json={"error": "bad_verification_code"}), httpx.Response(200)),
        github(httpx.Response(502), httpx.Response(200)),
        github(ok_token(), httpx.Response(401)),
        github(ok_token(), httpx.Response(200, json={"login": "octocat"})),
        github(ok_token(), httpx.Response(200, text="not json")),
        _transport_error,
    ],
)
def test_any_failure_is_provider_unavailable(handler: Handler) -> None:
    with pytest.raises(OAuthProviderUnavailable) as raised:
        provider(handler).exchange_code("code-secret-1")

    message = str(raised.value)
    assert "code-secret-1" not in message
    assert USER_TOKEN not in message
    assert "client-secret" not in message


EXPIRING_BODY: dict[str, Any] = {
    "access_token": USER_TOKEN,
    "expires_in": 28800,
    "refresh_token": "ghr_refresh-token-value",
    "refresh_token_expires_in": 15897600,
    "scope": "",
    "token_type": "bearer",
}


def test_an_expiring_token_comes_with_refresh_token_and_lifetimes() -> None:
    login = provider(github(httpx.Response(200, json=EXPIRING_BODY), httpx.Response(200, json=USER_JSON))).exchange_code("c")

    assert login.grant == ProviderGrant(Secret(USER_TOKEN), 28800, Secret("ghr_refresh-token-value"), 15897600)


def _refresh_with(response: httpx.Response, seen: list[httpx.Request] | None = None) -> ProviderGrant:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url == "https://github.com/login/oauth/access_token"
        if seen is not None:
            seen.append(request)
        return response

    return provider(handle).refresh(Secret("ghr_old-refresh"))


def test_refresh_posts_the_refresh_token_with_the_app_secret() -> None:
    seen: list[httpx.Request] = []
    body = EXPIRING_BODY | {"access_token": "ghu_new", "refresh_token": "ghr_new"}

    grant = _refresh_with(httpx.Response(200, json=body), seen)

    assert grant == ProviderGrant(Secret("ghu_new"), 28800, Secret("ghr_new"), 15897600)
    assert parse_qs(seen[0].content.decode()) == {
        "client_id": ["Iv1.client"],
        "client_secret": ["client-secret"],
        "grant_type": ["refresh_token"],
        "refresh_token": ["ghr_old-refresh"],
    }


def test_a_bad_refresh_token_is_rejected() -> None:
    body = {"error": "bad_refresh_token", "error_description": "The refresh token passed is incorrect or expired."}
    with pytest.raises(ProviderTokenRejected):
        _refresh_with(httpx.Response(200, json=body))


@pytest.mark.parametrize(
    "response",
    [
        # Неверный секрет приложения — наша настройка, а не сессия пользователя.
        httpx.Response(200, json={"error": "incorrect_client_credentials"}),
        httpx.Response(200, json={}),
        httpx.Response(200, json=None),
        httpx.Response(200, json=EXPIRING_BODY | {"expires_in": None, "refresh_token": 5}),
        httpx.Response(200, json=EXPIRING_BODY | {"expires_in": "soon"}),
        httpx.Response(200, json=EXPIRING_BODY | {"refresh_token_expires_in": True}),
        httpx.Response(503),
        httpx.Response(200, text="not json"),
    ],
)
def test_any_other_refresh_failure_is_provider_unavailable(response: httpx.Response) -> None:
    with pytest.raises(OAuthProviderUnavailable) as raised:
        _refresh_with(response)
    assert "ghr_old-refresh" not in str(raised.value)
    assert "client-secret" not in str(raised.value)


def test_a_network_failure_on_refresh_leaks_nothing() -> None:
    with pytest.raises(OAuthProviderUnavailable) as raised:
        provider(_transport_error).refresh(Secret("ghr_old-refresh"))
    assert raised.value.__cause__ is None
    assert "ghr_old-refresh" not in str(raised.value)


def test_the_provider_repr_hides_the_app_secret() -> None:
    assert "client-secret" not in repr(provider(_transport_error))
