"""`GitHubWebhookDeliveries` на записанных ответах GitHub REST, без сети.

Формы — из документации GitHub REST (версия API 2022-11-28), раздел «Webhooks»
приложения: «List deliveries for an app webhook», `GET /app/hook/deliveries` —
массив «Simple webhook delivery» (`id`, `guid`, `delivered_at`, `redelivery`,
`duration`, `status`, `status_code`, `event`, `action`, `installation_id`,
`repository_id`, `throttled_at`), постранично курсором из заголовка `Link`;
«Redeliver a delivery for an app webhook»,
`POST /app/hook/deliveries/{delivery_id}/attempts` — `202`. Обе — JWT приложения.
Против настоящего GitHub не проверено.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import jwt
import pytest

from app.domain.webhook_redelivery import WebhookDelivery
from app.infrastructure.vcs.errors import VcsAuthError, VcsError, VcsUnavailableError
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.infrastructure.vcs.github_deliveries import GitHubWebhookDeliveries
from tests.infrastructure.conftest import RsaKeyPair

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
Handler = Callable[[httpx.Request], httpx.Response]
BASE = "https://api.github.com"


def delivery_json(n: int, *, ago: timedelta = timedelta(hours=1), status_code: int = 500) -> dict[str, Any]:
    return {
        "id": n,
        "guid": f"0b989ba4-242f-11e5-81e1-{n:012d}",
        "delivered_at": (NOW - ago).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "redelivery": False,
        "duration": 0.27,
        "status": "OK" if status_code < 300 else "Invalid HTTP Response: 500",
        "status_code": status_code,
        "event": "pull_request",
        "action": "synchronize",
        "installation_id": 123,
        "repository_id": 456,
        "throttled_at": None,
    }


@pytest.fixture
def app_auth(rsa_key_pair: RsaKeyPair) -> GitHubAppAuth:
    unused = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    return GitHubAppAuth("4242", rsa_key_pair.private_pem, unused)


def make(handler: Handler, app_auth: GitHubAppAuth) -> GitHubWebhookDeliveries:
    client = httpx.Client(base_url=BASE, transport=httpx.MockTransport(handler))
    return GitHubWebhookDeliveries(client, app_auth)


def test_lists_deliveries_with_the_app_jwt(app_auth: GitHubAppAuth, rsa_key_pair: RsaKeyPair) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[delivery_json(1)])

    result = make(handler, app_auth).list_recent(NOW - timedelta(days=3))

    assert result == [
        WebhookDelivery(
            id=1,
            guid="0b989ba4-242f-11e5-81e1-000000000001",
            delivered_at=NOW - timedelta(hours=1),
            redelivery=False,
            status="Invalid HTTP Response: 500",
            status_code=500,
            event="pull_request",
            action="synchronize",
        )
    ]
    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == "/app/hook/deliveries"
    assert request.url.params["per_page"] == "100"
    assert "status" not in request.url.params  # успешные попытки нужны решению
    assert request.headers["Accept"] == "application/vnd.github+json"
    scheme, token = request.headers["Authorization"].split(" ")
    assert scheme == "Bearer"
    claims = jwt.decode(token, rsa_key_pair.public_pem, algorithms=["RS256"])
    assert claims["iss"] == "4242"


def test_follows_the_link_cursor_until_the_last_page(app_auth: GitHubAppAuth) -> None:
    pages = {
        None: ([delivery_json(3), delivery_json(2)], f'<{BASE}/app/hook/deliveries?per_page=100&cursor=v1_2>; rel="next"'),
        "v1_2": ([delivery_json(1)], None),
    }
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        seen.append(cursor)
        body, link = pages[cursor]
        return httpx.Response(200, json=body, headers={"Link": link} if link else {})

    result = make(handler, app_auth).list_recent(NOW - timedelta(days=3))

    assert [d.id for d in result] == [3, 2, 1]
    assert seen == [None, "v1_2"]


def test_stops_paging_once_a_page_reaches_past_since(app_auth: GitHubAppAuth) -> None:
    """Новые первыми: страница, дошедшая до `since`, последняя; старше — отброшены."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        body = [delivery_json(2, ago=timedelta(hours=1)), delivery_json(1, ago=timedelta(days=4))]
        return httpx.Response(200, json=body, headers={"Link": f'<{BASE}/app/hook/deliveries?cursor=next>; rel="next"'})

    result = make(handler, app_auth).list_recent(NOW - timedelta(days=3))

    assert [d.id for d in result] == [2]
    assert len(seen) == 1


def test_page_count_is_bounded(app_auth: GitHubAppAuth) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json=[delivery_json(calls)],
            headers={"Link": f'<{BASE}/app/hook/deliveries?cursor=c{calls}>; rel="next"'},
        )

    result = make(handler, app_auth).list_recent(NOW - timedelta(days=3))

    assert calls == 10
    assert len(result) == 10


def test_a_next_link_to_another_host_is_not_followed(app_auth: GitHubAppAuth) -> None:
    """JWT приложения уходит только на хост API."""
    hosts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(
            200, json=[delivery_json(1)], headers={"Link": '<https://evil.example/app/hook/deliveries?cursor=x>; rel="next"'}
        )

    with pytest.raises(VcsError):
        make(handler, app_auth).list_recent(NOW - timedelta(days=3))
    assert hosts == ["api.github.com"]


def test_redeliver_posts_an_attempt_with_the_app_jwt(app_auth: GitHubAppAuth) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202, json={})

    make(handler, app_auth).redeliver(12345678)

    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == "/app/hook/deliveries/12345678/attempts"
    assert request.headers["Authorization"].startswith("Bearer ")


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(429), VcsUnavailableError),
        (httpx.Response(502), VcsUnavailableError),
        (httpx.Response(403, headers={"x-ratelimit-remaining": "0"}), VcsUnavailableError),
        (httpx.Response(403, json={"message": "You have exceeded a secondary rate limit."}), VcsUnavailableError),
        (httpx.Response(401), VcsAuthError),
        (httpx.Response(422, json={"message": "Validation Failed"}), VcsError),
    ],
)
def test_failures_map_to_vcs_errors(app_auth: GitHubAppAuth, response: httpx.Response, error: type[VcsError]) -> None:
    gateway = make(lambda request: response, app_auth)
    with pytest.raises(error):
        gateway.redeliver(1)
    with pytest.raises(error):
        gateway.list_recent(NOW - timedelta(days=3))


def test_a_network_failure_is_unavailable(app_auth: GitHubAppAuth) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети", request=request)

    with pytest.raises(VcsUnavailableError):
        make(handler, app_auth).redeliver(1)


def test_an_unexpected_body_is_a_vcs_error(app_auth: GitHubAppAuth) -> None:
    gateway = make(lambda request: httpx.Response(200, json=[{"id": "x"}]), app_auth)
    with pytest.raises(VcsError, match="неожиданное тело"):
        gateway.list_recent(NOW - timedelta(days=3))


def test_an_unsigned_key_is_an_auth_error() -> None:
    """Пустой ключ App (стенд без реквизитов) — `VcsAuthError`, запроса нет."""
    unused = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    gateway = make(lambda request: pytest.fail("запроса быть не должно"), GitHubAppAuth("", "", unused))
    with pytest.raises(VcsAuthError):
        gateway.redeliver(1)


def test_reads_the_payload_of_a_delivery_with_the_app_jwt(app_auth: GitHubAppAuth) -> None:
    """«Get a delivery for an app webhook»: `request.payload` — объект или `null`."""
    seen: list[httpx.Request] = []
    payload = {
        "action": "synchronize",
        "pull_request": {"number": 6, "head": {"sha": "a" * 40}},
        "repository": {"id": 456, "full_name": "acme/payments"},
        "installation": {"id": 123},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = {
            **delivery_json(12345678),
            "url": "",
            "request": {"headers": {"X-GitHub-Event": "pull_request"}, "payload": payload},
            "response": {"headers": {}, "payload": "upstream timed out"},
        }
        return httpx.Response(200, json=body)

    assert make(handler, app_auth).delivery_payload(12345678) == payload

    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == "/app/hook/deliveries/12345678"
    assert request.headers["Authorization"].startswith("Bearer ")


def test_a_delivery_without_a_stored_payload_gives_none(app_auth: GitHubAppAuth) -> None:
    body = {**delivery_json(1), "request": {"headers": None, "payload": None}, "response": {"headers": None, "payload": None}}
    assert make(lambda request: httpx.Response(200, json=body), app_auth).delivery_payload(1) is None


def test_a_delivery_detail_failure_maps_like_the_others(app_auth: GitHubAppAuth) -> None:
    with pytest.raises(VcsUnavailableError):
        make(lambda request: httpx.Response(503), app_auth).delivery_payload(1)
    with pytest.raises(VcsError, match="неожиданное тело"):
        make(lambda request: httpx.Response(200, json={"request": "x"}), app_auth).delivery_payload(1)
