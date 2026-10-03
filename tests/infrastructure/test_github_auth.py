"""Аутентификация GitHub App на записанных ответах: JWT RS256 и токены.

Сеть подменена `httpx.MockTransport`. Ожидания взяты из документации GitHub
Apps — iss равен App ID, окно жизни JWT не длиннее 10 минут, токен приходит
вместе с `expires_at` — а не пересчитаны реализацией.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
import pytest

from app.infrastructure.vcs.errors import VcsAuthError, VcsUnavailableError
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from tests.infrastructure.conftest import RsaKeyPair

APP_ID = "123456"
INSTALLATION_ID = 512804923
FAR_FUTURE = "2099-01-01T00:00:00Z"

Handler = Callable[[httpx.Request], httpx.Response]


def make_auth(private_pem: str, handler: Handler) -> GitHubAppAuth:
    """Адаптер с подменённым транспортом; конструирование не касается сети."""
    client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )
    return GitHubAppAuth(APP_ID, private_pem, client)


def test_exchanges_rs256_jwt_for_installation_token(rsa_key_pair: RsaKeyPair) -> None:
    """POST идёт в endpoint токенов, авторизован JWT RS256 с iss=App ID."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["authorization"] = request.headers["Authorization"]
        return httpx.Response(
            200, json={"token": "ghs_1", "expires_at": FAR_FUTURE}
        )

    auth = make_auth(rsa_key_pair.private_pem, handler)

    assert auth.installation_token(INSTALLATION_ID) == "ghs_1"

    assert seen["method"] == "POST"
    assert seen["path"] == f"/app/installations/{INSTALLATION_ID}/access_tokens"
    scheme, json_web_token = seen["authorization"].split(" ", 1)
    assert scheme == "Bearer"
    claims = pyjwt.decode(
        json_web_token, rsa_key_pair.public_pem, algorithms=["RS256"]
    )
    assert claims["iss"] == APP_ID
    assert claims["exp"] - claims["iat"] <= 10 * 60


def test_token_is_cached_per_installation(rsa_key_pair: RsaKeyPair) -> None:
    """Пока не истёк, токен берётся из кэша: ноль новых HTTP-запросов."""
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(
            200, json={"token": "ghs_cached", "expires_at": FAR_FUTURE}
        )

    auth = make_auth(rsa_key_pair.private_pem, handler)

    first = auth.installation_token(INSTALLATION_ID)
    second = auth.installation_token(INSTALLATION_ID)

    assert first == second == "ghs_cached"
    assert len(posts) == 1


def test_expired_token_is_refreshed(rsa_key_pair: RsaKeyPair) -> None:
    """`expires_at` в прошлом — второй вызов делает новый POST."""
    tokens = iter(["ghs_old", "ghs_new"])
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(
            200, json={"token": next(tokens), "expires_at": "2020-01-01T00:00:00Z"}
        )

    auth = make_auth(rsa_key_pair.private_pem, handler)

    assert auth.installation_token(INSTALLATION_ID) == "ghs_old"
    assert auth.installation_token(INSTALLATION_ID) == "ghs_new"
    assert len(posts) == 2


def test_token_near_expiry_is_refreshed(rsa_key_pair: RsaKeyPair) -> None:
    """Меньше минуты до истечения — считаем просроченным: запас на запрос."""
    soon = (datetime.now(UTC) + timedelta(seconds=30)).isoformat()
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(200, json={"token": "ghs_soon", "expires_at": soon})

    auth = make_auth(rsa_key_pair.private_pem, handler)

    auth.installation_token(INSTALLATION_ID)
    auth.installation_token(INSTALLATION_ID)

    assert len(posts) == 2


def test_token_endpoint_error_raises_vcs_auth_error(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """Отказ endpoint'а токенов — исключение адаптера, а не httpx наружу."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": "Bad credentials"})

    auth = make_auth(rsa_key_pair.private_pem, handler)

    with pytest.raises(VcsAuthError):
        auth.installation_token(INSTALLATION_ID)


def test_token_transport_failure_raises_vcs_unavailable(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """Обрыв сети при обмене токена — `VcsUnavailableError` (502), не httpx."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    auth = make_auth(rsa_key_pair.private_pem, handler)

    with pytest.raises(VcsUnavailableError):
        auth.installation_token(INSTALLATION_ID)


def test_invalidate_forces_a_fresh_token(rsa_key_pair: RsaKeyPair) -> None:
    """После 401 кэш сбрасывают снаружи: следующий вызов делает новый POST."""
    tokens = iter(["ghs_revoked", "ghs_fresh"])
    posts: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posts.append(request)
        return httpx.Response(
            200, json={"token": next(tokens), "expires_at": FAR_FUTURE}
        )

    auth = make_auth(rsa_key_pair.private_pem, handler)

    assert auth.installation_token(INSTALLATION_ID) == "ghs_revoked"
    auth.invalidate(INSTALLATION_ID)
    assert auth.installation_token(INSTALLATION_ID) == "ghs_fresh"
    assert len(posts) == 2


def test_invalidate_of_unknown_installation_is_noop(rsa_key_pair: RsaKeyPair) -> None:
    """Сброс чужой/несуществующей инсталляции не падает."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"token": "ghs_1", "expires_at": FAR_FUTURE}
        )

    auth = make_auth(rsa_key_pair.private_pem, handler)
    auth.invalidate(INSTALLATION_ID)
    assert auth.installation_token(INSTALLATION_ID) == "ghs_1"


def test_unexpected_token_body_raises_vcs_auth_error(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """200 с телом не той формы (прокси, смена контракта) — VcsAuthError,
    а не KeyError/JSONDecodeError мимо `except VcsError` в use case."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>portal</html>")

    auth = make_auth(rsa_key_pair.private_pem, handler)

    with pytest.raises(VcsAuthError):
        auth.installation_token(INSTALLATION_ID)


def test_token_without_expires_at_raises_vcs_auth_error(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """Тело без `expires_at` — та же ошибка адаптера."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token": "ghs_1"})

    auth = make_auth(rsa_key_pair.private_pem, handler)

    with pytest.raises(VcsAuthError):
        auth.installation_token(INSTALLATION_ID)
