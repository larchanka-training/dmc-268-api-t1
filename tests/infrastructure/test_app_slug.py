"""Slug GitHub App для `install_url`: тот же `GET /app` и кэш, что у логина бота.

Контейнер настоящий, шлюз — `GitHubVcsGateway` над `httpx.MockTransport`:
видно, сколько раз ушёл `GET /app` и что сбой не роняет вызывающего.
"""

from collections.abc import Callable

import httpx
import pytest

from app.config import Settings
from app.infrastructure.container import Container, build_container
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth

from .conftest import RsaKeyPair

SLUG = "dmc-268-review"


def _settings(app_id: str = "", private_key: str = "") -> Settings:
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="test-secret",
        github_app_id=app_id,
        github_app_private_key=private_key,
    )


def _container(settings: Settings, handler: Callable[[httpx.Request], httpx.Response]) -> Container:
    container = build_container(settings)
    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    auth = GitHubAppAuth(settings.github_app_id, settings.github_app_private_key.get_secret_value(), client)
    # Тот же приём, что у контейнера: ленивый шлюз запоминается в frozen-объекте.
    object.__setattr__(container, "_vcs_gateway", GitHubVcsGateway(auth, client, sleep=lambda _: None))
    return container


def _answering(seen: list[str], response: Callable[[], httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return response()

    return handler


def test_the_slug_comes_from_get_app_once_per_process_and_is_shared_with_the_bot_login(
    rsa_key_pair: RsaKeyPair,
) -> None:
    seen: list[str] = []
    container = _container(
        _settings("123", rsa_key_pair.private_pem),
        _answering(seen, lambda: httpx.Response(200, json={"id": 123, "slug": SLUG})),
    )

    assert container.app_slug() == SLUG
    assert container.app_slug() == SLUG
    assert container.vcs_gateway().bot_login() == f"{SLUG}[bot]"
    assert seen == ["/app"]


def test_without_the_app_key_there_is_no_slug_and_no_request() -> None:
    seen: list[str] = []
    container = _container(_settings(), _answering(seen, lambda: httpx.Response(200, json={"slug": SLUG})))

    assert container.app_slug() is None
    assert seen == []


@pytest.mark.parametrize(
    "response",
    [
        lambda: httpx.Response(502, json={"message": "Bad Gateway"}),
        lambda: httpx.Response(200, json={"id": 123}),
        lambda: httpx.Response(200, json={"slug": ""}),
    ],
)
def test_a_failed_get_app_is_no_slug_and_is_asked_again(
    rsa_key_pair: RsaKeyPair, response: Callable[[], httpx.Response]
) -> None:
    seen: list[str] = []
    container = _container(_settings("123", rsa_key_pair.private_pem), _answering(seen, response))

    assert container.app_slug() is None
    assert container.app_slug() is None
    assert seen == ["/app", "/app"]


def test_an_unreachable_github_is_no_slug(rsa_key_pair: RsaKeyPair) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети", request=request)

    container = _container(_settings("123", rsa_key_pair.private_pem), handler)

    assert container.app_slug() is None


def test_a_key_that_does_not_sign_is_no_slug() -> None:
    seen: list[str] = []
    container = _container(_settings("123", "not a pem"), _answering(seen, lambda: httpx.Response(200)))

    assert container.app_slug() is None
    assert seen == []
