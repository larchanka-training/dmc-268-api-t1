"""`GitHubVcsGateway` на записанных ответах: дифф, метаданные, backoff.

Один `httpx.MockTransport` обслуживает и endpoint токенов, и endpoint
pull request'а, так что адаптер прогоняется целиком. Паузы backoff
собираются фейковым sleep — ожидаемые значения взяты из конструктора
(0.5 c * 2^n), а не из таймера.
"""

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.domain.entities import PRMetadata
from app.domain.enums import MergeRequestState
from app.infrastructure.vcs.errors import (
    VcsAuthError,
    VcsError,
    VcsUnavailableError,
)
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from tests.infrastructure.conftest import RsaKeyPair

APP_ID = "123456"
INSTALLATION_ID = 512804923
FAR_FUTURE = "2099-01-01T00:00:00Z"

Handler = Callable[[httpx.Request], httpx.Response]

DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"

PR_JSON: dict[str, Any] = {
    "number": 6,
    "state": "open",
    "title": "feat: приём вебхуков и VCS-шлюз",
    "user": {"login": "ilyassakhanov"},
    "head": {"sha": "a" * 40, "ref": "feat/webhook-intake"},
    "base": {"sha": "b" * 40, "ref": "develop"},
}

EXPECTED_METADATA = PRMetadata(
    number=6,
    head_sha="a" * 40,
    base_sha="b" * 40,
    title="feat: приём вебхуков и VCS-шлюз",
    author="ilyassakhanov",
    source_branch="feat/webhook-intake",
    target_branch="develop",
    state=MergeRequestState.OPEN,
)


def make_gateway(
    rsa_key_pair: RsaKeyPair, handler: Handler, sleeps: list[float]
) -> GitHubVcsGateway:
    client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )
    auth = GitHubAppAuth(APP_ID, rsa_key_pair.private_pem, client)
    return GitHubVcsGateway(auth, client, sleep=sleeps.append)


def responding(
    pr_status: list[int],
    pr_body: httpx.Response,
    seen: list[httpx.Request],
) -> Handler:
    """Handler: токен — всегда 200, PR — по списку кодов, последний тело повторяет."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(
                200, json={"token": "ghs_test", "expires_at": FAR_FUTURE}
            )
        seen.append(request)
        index = min(len(seen) - 1, len(pr_status) - 1)
        status = pr_status[index]
        if status >= 400:
            return httpx.Response(status, json={"message": "err"})
        return httpx.Response(status, content=pr_body.content, headers=pr_body.headers)

    return handler


def test_fetch_diff_returns_recorded_body_verbatim(rsa_key_pair: RsaKeyPair) -> None:
    """Дифф — тело ответа без преобразований, с правильными заголовками."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair,
        responding(
            [200], httpx.Response(200, text=DIFF, headers={"Content-Type": "text/plain"}), seen
        ),
        sleeps,
    )

    assert gateway.fetch_diff("larchanka-training/dmc-268-api-t1", 6, INSTALLATION_ID) == DIFF

    get = seen[-1]
    assert get.method == "GET"
    assert get.url.path == "/repos/larchanka-training/dmc-268-api-t1/pulls/6"
    assert get.headers["Accept"] == "application/vnd.github.v3.diff"
    assert get.headers["Authorization"] == "Bearer ghs_test"
    assert sleeps == []


def test_fetch_pr_metadata_maps_fields_one_to_one(rsa_key_pair: RsaKeyPair) -> None:
    """Поля GitHub переводятся в `PRMetadata` ровно, без бизнес-правил."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair,
        responding(
            [200], httpx.Response(200, json=PR_JSON), seen
        ),
        sleeps,
    )

    metadata = gateway.fetch_pr_metadata(
        "larchanka-training/dmc-268-api-t1", 6, INSTALLATION_ID
    )

    assert metadata == EXPECTED_METADATA
    assert seen[-1].headers["Accept"] == "application/vnd.github+json"


@pytest.mark.parametrize(
    ("pr_state", "merged", "expected"),
    [
        pytest.param("open", False, MergeRequestState.OPEN, id="open"),
        pytest.param("closed", False, MergeRequestState.CLOSED, id="closed"),
        pytest.param("closed", True, MergeRequestState.MERGED, id="closed+merged"),
    ],
)
def test_pr_state_is_translated_to_domain_enum(
    rsa_key_pair: RsaKeyPair, pr_state: str, merged: bool, expected: MergeRequestState
) -> None:
    """Перевод состояний описан в самом `MergeRequestState`, адаптер следует ему."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    payload = {**PR_JSON, "state": pr_state, "merged": merged}
    gateway = make_gateway(
        rsa_key_pair, responding([200], httpx.Response(200, json=payload), seen), sleeps
    )

    metadata = gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID)

    assert metadata.state == expected


def test_5xx_is_retried_with_growing_pauses(rsa_key_pair: RsaKeyPair) -> None:
    """500 → 500 → успех: паузы растут 0.5 c, 1 c; результат возвращён."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair,
        responding(
            [500, 500, 200],
            httpx.Response(200, text=DIFF, headers={"Content-Type": "text/plain"}),
            seen,
        ),
        sleeps,
    )

    assert gateway.fetch_diff("owner/repo", 6, INSTALLATION_ID) == DIFF
    assert len(seen) == 3
    assert sleeps == [0.5, 1.0]


def test_429_is_retried(rsa_key_pair: RsaKeyPair) -> None:
    """Rate limit — повторяемая ошибка: одна пауза, затем успех."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair,
        responding(
            [429, 200],
            httpx.Response(200, text=DIFF, headers={"Content-Type": "text/plain"}),
            seen,
        ),
        sleeps,
    )

    assert gateway.fetch_diff("owner/repo", 6, INSTALLATION_ID) == DIFF
    assert len(seen) == 2
    assert sleeps == [0.5]


def test_exhausted_retries_raise_vcs_unavailable(rsa_key_pair: RsaKeyPair) -> None:
    """Три 5xx подряд — `VcsUnavailableError`: последняя попытка без паузы."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair,
        responding([500, 500, 500], httpx.Response(200, json={}), seen),
        sleeps,
    )

    with pytest.raises(VcsUnavailableError):
        gateway.fetch_diff("owner/repo", 6, INSTALLATION_ID)

    assert len(seen) == 3
    assert sleeps == [0.5, 1.0]


def test_404_raises_immediately_without_retry(rsa_key_pair: RsaKeyPair) -> None:
    """Клиентская ошибка не повторяемая: один запрос, ноль пауз."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair,
        responding([404], httpx.Response(200, json={}), seen),
        sleeps,
    )

    with pytest.raises(VcsError):
        gateway.fetch_diff("owner/repo", 6, INSTALLATION_ID)

    assert len(seen) == 1
    assert sleeps == []


def failing_transport(
    connect_fails: bool, seen: list[httpx.Request]
) -> Handler:
    """Handler: токен выдаётся, PR-запрос роняет транспортной ошибкой httpx."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(
                200, json={"token": "ghs_test", "expires_at": FAR_FUTURE}
            )
        seen.append(request)
        error: httpx.RequestError = (
            httpx.ConnectError("connection refused", request=request)
            if connect_fails
            else httpx.ReadTimeout("timed out", request=request)
        )
        raise error

    return handler


@pytest.mark.parametrize("connect_fails", [True, False], ids=["connect", "timeout"])
def test_transport_failure_of_diff_raises_vcs_unavailable(
    rsa_key_pair: RsaKeyPair, connect_fails: bool
) -> None:
    """Обрыв сети на GET диффа — `VcsUnavailableError` (502), не httpx наружу."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(rsa_key_pair, failing_transport(connect_fails, seen), sleeps)

    with pytest.raises(VcsUnavailableError):
        gateway.fetch_diff("owner/repo", 6, INSTALLATION_ID)

    assert len(seen) == 1
    assert sleeps == []


def test_transport_failure_of_metadata_raises_vcs_unavailable(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """Обрыв сети на GET метаданных — та же 502-совместимая ошибка адаптера."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(rsa_key_pair, failing_transport(True, seen), sleeps)

    with pytest.raises(VcsUnavailableError):
        gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID)

    assert len(seen) == 1


def html_portal_handler(seen: list[httpx.Request]) -> Handler:
    """Токен — 200, PR — 200 с HTML вместо JSON: прокси или captive portal."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(
                200, json={"token": "ghs_test", "expires_at": FAR_FUTURE}
            )
        seen.append(request)
        return httpx.Response(200, text="<html>login page</html>")

    return handler


def test_metadata_200_with_html_body_raises_vcs_error(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """Неожиданное тело не уходит наружу JSONDecodeError/KeyError: наверху 502."""
    seen: list[httpx.Request] = []
    gateway = make_gateway(rsa_key_pair, html_portal_handler(seen), [])

    with pytest.raises(VcsError):
        gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID)

    assert len(seen) == 1


def test_incomplete_metadata_json_raises_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    """Форма ответа, сменившаяся у GitHub (нет `user.login`), — тоже VcsError."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(
                200, json={"token": "ghs_test", "expires_at": FAR_FUTURE}
            )
        seen.append(request)
        return httpx.Response(200, json={"number": 6, "head": {"sha": "a" * 40}})

    sleeps: list[float] = []
    gateway = make_gateway(rsa_key_pair, handler, sleeps)

    with pytest.raises(VcsError):
        gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID)

    assert sleeps == []


def revoked_token_handler(seen: list[httpx.Request]) -> Handler:
    """Первый токен просрочен для GitHub'а: 401; после перевыпуска — 200."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.startswith("/app/installations/"):
            pr_seen = [r for r in seen if not r.url.path.startswith("/app/installations/")]
            token = "ghs_stale" if len(pr_seen) == 0 else "ghs_fresh"
            return httpx.Response(
                200, json={"token": token, "expires_at": FAR_FUTURE}
            )
        if request.headers["Authorization"] == "Bearer ghs_stale":
            return httpx.Response(401, json={"message": "Bad credentials"})
        return httpx.Response(200, text=DIFF)

    return handler


def test_401_invalidates_cached_token_and_retries_once(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """Отозванный до истечения TTL токен не держит инсталляцию в 502:
    после 401 кэш сброшен, новый токен проходит."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(rsa_key_pair, revoked_token_handler(seen), sleeps)

    assert gateway.fetch_diff("owner/repo", 6, INSTALLATION_ID) == DIFF

    pr_requests = [r for r in seen if not r.url.path.startswith("/app/installations/")]
    token_requests = [r for r in seen if r.url.path.startswith("/app/installations/")]
    assert len(token_requests) == 2  # перевыпуск после сброса кэша
    assert len(pr_requests) == 2  # повтор после 401, без пауз backoff
    assert sleeps == []


def test_second_401_after_refresh_raises_vcs_auth_error(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """Новый токен тоже отклонён — честная ошибка аутентификации, не цикл."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(
                200, json={"token": "ghs_stale", "expires_at": FAR_FUTURE}
            )
        seen.append(request)
        return httpx.Response(401, json={"message": "Bad credentials"})

    sleeps: list[float] = []
    gateway = make_gateway(rsa_key_pair, handler, sleeps)

    with pytest.raises(VcsAuthError):
        gateway.fetch_diff("owner/repo", 6, INSTALLATION_ID)

    pr_requests = [r for r in seen if not r.url.path.startswith("/app/installations/")]
    assert len(pr_requests) == 2
    assert sleeps == []
