"""`GitHubOAuthProvider` на записанных ответах: обмен `code` и `GET /user`."""

from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.domain.entities import OAuthProfile
from app.domain.login import OAuthProviderUnavailable
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

    profile = provider(record).exchange_code("code-1")

    assert profile == OAuthProfile("583231", "octocat", "The Octocat", USER_JSON["avatar_url"])
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

    profile = provider(github(ok_token(), httpx.Response(200, json=user))).exchange_code("c")

    assert profile.name is None


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
