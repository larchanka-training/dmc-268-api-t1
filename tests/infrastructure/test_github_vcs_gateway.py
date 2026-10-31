"""`GitHubVcsGateway` на записанных ответах: дифф, метаданные, backoff.

Один `httpx.MockTransport` обслуживает и endpoint токенов, и endpoint
pull request'а, так что адаптер прогоняется целиком. Паузы backoff
собираются фейковым sleep — ожидаемые значения взяты из конструктора
(0.5 c * 2^n), а не из таймера.
"""

import gzip
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest

from app.domain import vcs_errors
from app.domain.entities import PRMetadata
from app.domain.enums import MergeRequestState
from app.domain.vcs_errors import VcsFileTooLargeError
from app.infrastructure.vcs.errors import (
    VcsAuthError,
    VcsError,
    VcsNotFoundError,
    VcsUnavailableError,
)
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from tests.infrastructure.conftest import RsaKeyPair

APP_ID = "123456"
INSTALLATION_ID = 512804923
FAR_FUTURE = "2099-01-01T00:00:00Z"
LIMIT = 1_000

Handler = Callable[[httpx.Request], httpx.Response]

DIFF = "diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-old\n+new\n"

PR_JSON: dict[str, Any] = {
    "number": 6,
    "state": "open",
    "title": "feat: приём вебхуков и VCS-шлюз",
    "body": "Принимает вебхуки и ставит прогон в очередь.",
    "user": {"login": "ilyassakhanov"},
    "head": {"sha": "a" * 40, "ref": "feat/webhook-intake"},
    "base": {"sha": "b" * 40, "ref": "develop"},
}

EXPECTED_METADATA = PRMetadata(
    number=6,
    head_sha="a" * 40,
    base_sha="b" * 40,
    title="feat: приём вебхуков и VCS-шлюз",
    description="Принимает вебхуки и ставит прогон в очередь.",
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
    """Дифф — тело ответа без преобразований, с правильными заголовками.

    Дифф запрашивается compare-парой SHA, а не текущим head `/pulls/{n}`:
    после `synchronize` между доставкой и обработкой воркер отревьюит
    доставленный коммит, а не актуальный.
    """
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair,
        responding(
            [200], httpx.Response(200, text=DIFF, headers={"Content-Type": "text/plain"}), seen
        ),
        sleeps,
    )

    base_sha, head_sha = "b" * 40, "a" * 40
    assert (
        gateway.fetch_diff(
            "larchanka-training/dmc-268-api-t1", base_sha, head_sha, INSTALLATION_ID, max_bytes=LIMIT
        )
        == DIFF
    )

    get = seen[-1]
    assert get.method == "GET"
    assert (
        get.url.path
        == f"/repos/larchanka-training/dmc-268-api-t1/compare/{base_sha}...{head_sha}"
    )
    assert get.headers["Accept"] == "application/vnd.github.v3.diff"
    assert get.headers["Authorization"] == "Bearer ghs_test"
    assert sleeps == []


def test_fetch_diff_declared_length_over_limit_is_refused(rsa_key_pair: RsaKeyPair) -> None:
    """Дифф с объявленной длиной больше лимита — отказ без чтения тела, без повторов."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair,
        responding([200], httpx.Response(200, content=b"+" * (LIMIT + 1)), seen),
        sleeps,
    )

    with pytest.raises(VcsFileTooLargeError):
        gateway.fetch_diff("owner/repo", "b" * 40, "a" * 40, INSTALLATION_ID, max_bytes=LIMIT)

    assert len(seen) == 1
    assert sleeps == []


def test_fetch_diff_stream_without_length_is_cut_at_limit(rsa_key_pair: RsaKeyPair) -> None:
    """Дифф без `Content-Length` читается потоком и не дочитывается дальше лимита."""
    produced: list[int] = []

    def chunks() -> Iterator[bytes]:
        for _ in range(100):
            produced.append(1)
            yield b"+" * 100

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        return httpx.Response(200, content=chunks())

    gateway = make_gateway(rsa_key_pair, handler, [])

    with pytest.raises(VcsFileTooLargeError):
        gateway.fetch_diff("owner/repo", "b" * 40, "a" * 40, INSTALLATION_ID, max_bytes=LIMIT)

    # Оборвано на 11-м куске из 100: остальное не читалось.
    assert len(produced) == 11


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


def test_pr_without_a_description_has_an_empty_one(rsa_key_pair: RsaKeyPair) -> None:
    """GitHub отдаёт `body: null` для PR без описания."""
    seen: list[httpx.Request] = []
    gateway = make_gateway(
        rsa_key_pair, responding([200], httpx.Response(200, json={**PR_JSON, "body": None}), seen), []
    )

    assert gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID).description == ""


def _commits_handler(pages: list[list[dict[str, Any]]], seen: list[httpx.Request]) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        seen.append(request)
        return httpx.Response(200, json=pages[int(request.url.params["page"]) - 1])

    return handler


def _commit(n: int) -> dict[str, Any]:
    return {"sha": f"{n:040d}", "commit": {"message": f"коммит {n}"}}


def test_commit_messages_come_newest_first_across_pages(rsa_key_pair: RsaKeyPair) -> None:
    """GitHub отдаёт коммиты PR от старого к новому, не больше 250, по 100 на страницу."""
    pages = [[_commit(n) for n in range(start, start + size)] for start, size in ((1, 100), (101, 100), (201, 50))]
    seen: list[httpx.Request] = []
    gateway = make_gateway(rsa_key_pair, _commits_handler(pages, seen), [])

    messages = gateway.fetch_pr_commit_messages("owner/repo", 6, INSTALLATION_ID)

    assert len(messages) == 250
    assert messages[:2] == ("коммит 250", "коммит 249")
    assert messages[-1] == "коммит 1"
    assert [r.url.path for r in seen] == ["/repos/owner/repo/pulls/6/commits"] * 3
    assert [r.url.params["per_page"] for r in seen] == ["100"] * 3


def test_commit_without_a_message_is_a_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    seen: list[httpx.Request] = []
    gateway = make_gateway(rsa_key_pair, _commits_handler([[{"sha": "1" * 40}]], seen), [])

    with pytest.raises(VcsError, match="коммит"):
        gateway.fetch_pr_commit_messages("owner/repo", 6, INSTALLATION_ID)


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

    assert gateway.fetch_diff("owner/repo", "b" * 40, "a" * 40, INSTALLATION_ID, max_bytes=LIMIT) == DIFF
    assert len(seen) == 3
    assert sleeps == [0.5, 1.0]


def _limited(status: int, headers: dict[str, str], seen: list[httpx.Request]) -> Handler:
    """Первый запрос к PR - отказ лимита с заголовками, следующий - успех."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(status, headers=headers, json={"message": "rate limit"})
        return httpx.Response(200, json=PR_JSON)

    return handler


def _clocked_gateway(
    rsa_key_pair: RsaKeyPair, handler: Handler, sleeps: list[float], **kwargs: Any
) -> GitHubVcsGateway:
    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    auth = GitHubAppAuth(APP_ID, rsa_key_pair.private_pem, client)
    return GitHubVcsGateway(auth, client, sleep=sleeps.append, clock=lambda: 1_800_000_000.0, **kwargs)


def test_429_waits_for_retry_after(rsa_key_pair: RsaKeyPair) -> None:
    """Вторичный лимит: пауза - ровно `Retry-After` секунд, затем повтор (#185)."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = _clocked_gateway(rsa_key_pair, _limited(429, {"Retry-After": "7"}, seen), sleeps)

    assert gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID) == EXPECTED_METADATA
    assert len(seen) == 2
    assert sleeps == [7.0]


def test_exhausted_primary_limit_waits_until_the_reset(rsa_key_pair: RsaKeyPair) -> None:
    """403 с `x-ratelimit-remaining: 0`: ждём до `x-ratelimit-reset` (эпоха, UTC) и секунду сверху."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(1_800_000_000 + 30)}
    gateway = _clocked_gateway(rsa_key_pair, _limited(403, headers, seen), sleeps)

    assert gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID) == EXPECTED_METADATA
    assert sleeps == [31.0]


def test_rate_limit_without_headers_waits_a_minute(rsa_key_pair: RsaKeyPair) -> None:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = _clocked_gateway(rsa_key_pair, _limited(429, {}, seen), sleeps)

    assert gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID) == EXPECTED_METADATA
    assert sleeps == [60.0]


def test_rate_limit_wait_over_the_budget_fails_without_sleeping(rsa_key_pair: RsaKeyPair) -> None:
    """Ждать дольше бюджета нельзя: воркер не висит до сброса часового лимита."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(1_800_000_000 + 3_000)}
    gateway = _clocked_gateway(
        rsa_key_pair, _limited(403, headers, seen), sleeps, rate_limit_budget_seconds=60.0
    )

    with pytest.raises(VcsUnavailableError):
        gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID)
    assert len(seen) == 1
    assert sleeps == []


def test_forbidden_without_rate_limit_signs_is_not_retried(rsa_key_pair: RsaKeyPair) -> None:
    """403 без признаков лимита - отказ в доступе, а не лимит: повтора нет."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        seen.append(request)
        return httpx.Response(403, json={"message": "Resource not accessible by integration"})

    gateway = _clocked_gateway(rsa_key_pair, handler, sleeps)

    with pytest.raises(VcsError) as caught:
        gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID)
    assert not isinstance(caught.value, VcsUnavailableError)
    assert len(seen) == 1
    assert sleeps == []


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
        gateway.fetch_diff("owner/repo", "b" * 40, "a" * 40, INSTALLATION_ID, max_bytes=LIMIT)

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

    with pytest.raises(VcsNotFoundError) as caught:
        gateway.fetch_diff("owner/repo", "b" * 40, "a" * 40, INSTALLATION_ID, max_bytes=LIMIT)

    # 404 на compare не различает коммит и репозиторий: код - PR_NOT_FOUND.
    assert caught.value.code == "PR_NOT_FOUND"
    assert len(seen) == 1
    assert sleeps == []


def test_404_on_compare_raises_the_domain_not_found(rsa_key_pair: RsaKeyPair) -> None:
    """Слой приложения ловит «не найдено» доменным классом, не зная адаптера."""
    gateway = make_gateway(rsa_key_pair, responding([404], httpx.Response(200, json={}), []), [])

    with pytest.raises(vcs_errors.VcsNotFoundError):
        gateway.fetch_diff("owner/repo", "b" * 40, "a" * 40, INSTALLATION_ID, max_bytes=LIMIT)


def _moved(seen: list[httpx.Request]) -> Handler:
    """Handler: репозиторий переименован — API отвечает `301` с телом-ссылкой."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        seen.append(request)
        return httpx.Response(
            301,
            json={"message": "Moved Permanently", "url": "https://api.github.com/repositories/1"},
            headers={"location": "https://api.github.com/repositories/1"},
        )

    return handler


def test_redirect_on_compare_is_a_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    """`301` на compare — ошибка, а не тело редиректа под видом диффа."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(rsa_key_pair, _moved(seen), sleeps)

    with pytest.raises(VcsError) as caught:
        gateway.fetch_diff("owner/repo", "b" * 40, "a" * 40, INSTALLATION_ID, max_bytes=LIMIT)

    assert not isinstance(caught.value, (VcsNotFoundError, VcsUnavailableError))
    assert len(seen) == 1
    assert sleeps == []


def test_redirect_on_metadata_is_a_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    """`301` на чтение PR — та же ошибка, без повторов."""
    seen: list[httpx.Request] = []
    gateway = make_gateway(rsa_key_pair, _moved(seen), [])

    with pytest.raises(VcsError):
        gateway.fetch_pr_metadata("owner/repo", 6, INSTALLATION_ID)

    assert len(seen) == 1


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
        gateway.fetch_diff("owner/repo", "b" * 40, "a" * 40, INSTALLATION_ID, max_bytes=LIMIT)

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

    assert gateway.fetch_diff("owner/repo", "b" * 40, "a" * 40, INSTALLATION_ID, max_bytes=LIMIT) == DIFF

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
        gateway.fetch_diff("owner/repo", "b" * 40, "a" * 40, INSTALLATION_ID, max_bytes=LIMIT)

    pr_requests = [r for r in seen if not r.url.path.startswith("/app/installations/")]
    assert len(pr_requests) == 2
    assert sleeps == []


def test_fetch_file_returns_raw_content_at_sha(rsa_key_pair: RsaKeyPair) -> None:
    """Содержимое — тело ответа raw; коммит — параметром `ref`, а не веткой."""
    seen: list[httpx.Request] = []
    gateway = make_gateway(
        rsa_key_pair,
        responding([200], httpx.Response(200, text="## Rule\n", headers={"Content-Type": "application/vnd.github.raw+json; charset=utf-8"}), seen),
        [],
    )

    content = gateway.fetch_file("owner/repo", ".review/rules.md", "b" * 40, INSTALLATION_ID, max_bytes=LIMIT)

    assert content == "## Rule\n"
    (request,) = seen
    assert request.url.path == "/repos/owner/repo/contents/.review/rules.md"
    assert request.url.params["ref"] == "b" * 40
    assert request.headers["Accept"] == "application/vnd.github.raw+json"
    assert request.headers["Authorization"] == "Bearer ghs_test"


def test_fetch_file_directory_means_no_file(rsa_key_pair: RsaKeyPair) -> None:
    """На каталог GitHub отвечает 200 с JSON-листингом (проверено вживую): файла нет."""
    listing = httpx.Response(
        200, json=[{"name": "rules.md", "type": "file"}], headers={"Content-Type": "application/json; charset=utf-8"}
    )
    gateway = make_gateway(rsa_key_pair, responding([200], listing, []), [])

    assert gateway.fetch_file("owner/repo", ".review", "b" * 40, INSTALLATION_ID, max_bytes=LIMIT) is None


def test_fetch_file_404_means_no_file(rsa_key_pair: RsaKeyPair) -> None:
    """Отсутствие файла — штатный исход: `None`, без повторов и пауз."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair, responding([404], httpx.Response(200, text=""), seen), sleeps
    )

    assert gateway.fetch_file("owner/repo", "missing.md", "b" * 40, INSTALLATION_ID, max_bytes=LIMIT) is None
    assert len(seen) == 1
    assert sleeps == []


def test_fetch_file_escapes_path_from_the_diff(rsa_key_pair: RsaKeyPair) -> None:
    """Путь пишет автор PR: `?` и `#` в имени файла не превращаются в запрос."""
    seen: list[httpx.Request] = []
    gateway = make_gateway(
        rsa_key_pair, responding([200], httpx.Response(200, text="x"), seen), []
    )

    gateway.fetch_file("owner/repo", "dir/a b?c#d.py", "b" * 40, INSTALLATION_ID, max_bytes=LIMIT)

    (request,) = seen
    assert request.url.raw_path.decode().startswith(
        "/repos/owner/repo/contents/dir/a%20b%3Fc%23d.py?"
    )
    assert dict(request.url.params) == {"ref": "b" * 40}


def test_fetch_file_5xx_is_retried_then_raises(rsa_key_pair: RsaKeyPair) -> None:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair, responding([503], httpx.Response(200, text=""), seen), sleeps
    )

    with pytest.raises(VcsUnavailableError):
        gateway.fetch_file("owner/repo", "a.py", "b" * 40, INSTALLATION_ID, max_bytes=LIMIT)

    assert len(seen) == 3
    assert sleeps == [0.5, 1.0]


def test_fetch_file_403_is_a_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    """Нет разрешения Contents: read — сбой, а не «файла нет»."""
    seen: list[httpx.Request] = []
    gateway = make_gateway(
        rsa_key_pair, responding([403], httpx.Response(200, text=""), seen), []
    )

    with pytest.raises(VcsError):
        gateway.fetch_file("owner/repo", "a.py", "b" * 40, INSTALLATION_ID, max_bytes=LIMIT)


def file_handler(build: Callable[[], httpx.Response], seen: list[httpx.Request]) -> Handler:
    """Handler: токен выдаётся, на запрос файла — ответ, собранный `build`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        seen.append(request)
        return build()

    return handler


def test_fetch_file_declared_length_over_limit_is_refused(rsa_key_pair: RsaKeyPair) -> None:
    """Объявленная длина больше лимита — отказ без чтения тела, без повторов."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(
        rsa_key_pair, file_handler(lambda: httpx.Response(200, content=b"x" * (LIMIT + 1)), seen), sleeps
    )

    with pytest.raises(VcsFileTooLargeError):
        gateway.fetch_file("owner/repo", "big.py", "b" * 40, INSTALLATION_ID, max_bytes=LIMIT)

    assert len(seen) == 1
    assert sleeps == []


def test_fetch_file_stream_without_length_is_cut_at_limit(rsa_key_pair: RsaKeyPair) -> None:
    """Без `Content-Length` тело читается потоком и обрывается на лимите."""
    produced: list[int] = []

    def chunks() -> Iterator[bytes]:
        for _ in range(100):
            produced.append(1)
            yield b"x" * 100

    seen: list[httpx.Request] = []
    gateway = make_gateway(
        rsa_key_pair, file_handler(lambda: httpx.Response(200, content=chunks()), seen), []
    )

    with pytest.raises(VcsFileTooLargeError):
        gateway.fetch_file("owner/repo", "big.py", "b" * 40, INSTALLATION_ID, max_bytes=LIMIT)

    # Оборвано на 11-м куске из 100: остальное не читалось.
    assert len(produced) < 100


def test_fetch_file_compressed_bomb_is_cut_after_decoding(rsa_key_pair: RsaKeyPair) -> None:
    """Лимит — на распакованные байты: маленький gzip с большим телом не проходит."""
    packed = gzip.compress(b"x" * (LIMIT * 50))
    assert len(packed) < LIMIT
    seen: list[httpx.Request] = []
    gateway = make_gateway(
        rsa_key_pair,
        file_handler(
            lambda: httpx.Response(200, content=packed, headers={"Content-Encoding": "gzip"}), seen
        ),
        [],
    )

    with pytest.raises(VcsFileTooLargeError):
        gateway.fetch_file("owner/repo", "bomb.py", "b" * 40, INSTALLATION_ID, max_bytes=LIMIT)


def test_fetch_file_compressed_body_within_limit_is_decoded(rsa_key_pair: RsaKeyPair) -> None:
    packed = gzip.compress("привет".encode())
    seen: list[httpx.Request] = []
    gateway = make_gateway(
        rsa_key_pair,
        file_handler(
            lambda: httpx.Response(
                200,
                content=packed,
                headers={"Content-Encoding": "gzip", "Content-Type": "application/vnd.github.raw+json; charset=utf-8"},
            ),
            seen,
        ),
        [],
    )

    assert gateway.fetch_file("owner/repo", "a.py", "b" * 40, INSTALLATION_ID, max_bytes=LIMIT) == "привет"


SHA_RESPONSE = httpx.Response(
    200, text="c" * 40, headers={"Content-Type": "application/vnd.github.sha; charset=utf-8"}
)


def test_resolve_branch_sha_returns_sha_from_text_body(rsa_key_pair: RsaKeyPair) -> None:
    """Media type `vnd.github.sha` — тело ответа sha текстом (проверено вживую),
    запрос — под installation token."""
    seen: list[httpx.Request] = []
    gateway = make_gateway(rsa_key_pair, responding([200], SHA_RESPONSE, seen), [])

    assert gateway.resolve_branch_sha("owner/repo", "develop", INSTALLATION_ID) == "c" * 40

    (request,) = seen
    assert request.method == "GET"
    assert request.url.path == "/repos/owner/repo/commits/heads/develop"
    assert request.headers["Accept"] == "application/vnd.github.sha"
    assert request.headers["Authorization"] == "Bearer ghs_test"


def test_resolve_branch_sha_escapes_branch_but_keeps_slashes(rsa_key_pair: RsaKeyPair) -> None:
    """`/` в имени ветки GitHub принимает как есть (проверено вживую), а `#` и `%`
    из имени ветки не превращаются во фрагмент и не ломают путь."""
    seen: list[httpx.Request] = []
    gateway = make_gateway(rsa_key_pair, responding([200], SHA_RESPONSE, seen), [])

    gateway.resolve_branch_sha("owner/repo", "release/2.0#rc%1", INSTALLATION_ID)

    (request,) = seen
    assert request.url.raw_path.decode() == "/repos/owner/repo/commits/heads/release/2.0%23rc%251"


def test_resolve_branch_sha_404_is_not_found(rsa_key_pair: RsaKeyPair) -> None:
    """Репозитория нет или он недоступен установке — `VcsNotFoundError`, без повторов."""
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(rsa_key_pair, responding([404], SHA_RESPONSE, seen), sleeps)

    with pytest.raises(VcsNotFoundError):
        gateway.resolve_branch_sha("owner/repo", "develop", INSTALLATION_ID)

    assert len(seen) == 1
    assert sleeps == []


def test_resolve_branch_sha_missing_branch_is_a_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    """Ветки нет — GitHub отвечает 422 «No commit found for SHA» (проверено вживую):
    сбой адаптера, без повторов."""
    seen: list[httpx.Request] = []
    gateway = make_gateway(rsa_key_pair, responding([422], SHA_RESPONSE, seen), [])

    with pytest.raises(VcsError):
        gateway.resolve_branch_sha("owner/repo", "gone", INSTALLATION_ID)

    assert len(seen) == 1


def test_resolve_branch_sha_5xx_is_retried_then_unavailable(rsa_key_pair: RsaKeyPair) -> None:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    gateway = make_gateway(rsa_key_pair, responding([503], SHA_RESPONSE, seen), sleeps)

    with pytest.raises(VcsUnavailableError):
        gateway.resolve_branch_sha("owner/repo", "develop", INSTALLATION_ID)

    assert len(seen) == 3
    assert sleeps == [0.5, 1.0]


def test_resolve_branch_sha_redirect_is_a_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    """Переименованный репозиторий отвечает 301 — переход шлюз не делает."""
    redirect = httpx.Response(301, headers={"Location": "https://api.github.com/repositories/1"})
    gateway = make_gateway(rsa_key_pair, responding([301], redirect, []), [])

    with pytest.raises(VcsError) as raised:
        gateway.resolve_branch_sha("owner/repo", "develop", INSTALLATION_ID)

    assert not isinstance(raised.value, VcsNotFoundError | VcsUnavailableError)


def test_resolve_branch_sha_non_sha_body_is_a_vcs_error(rsa_key_pair: RsaKeyPair) -> None:
    """Прокси отдал HTML или GitHub проигнорировал media type — не sha, сбой адаптера."""
    html = httpx.Response(200, text="<html>oops</html>", headers={"Content-Type": "text/html"})
    gateway = make_gateway(rsa_key_pair, responding([200], html, []), [])

    with pytest.raises(VcsError):
        gateway.resolve_branch_sha("owner/repo", "develop", INSTALLATION_ID)
