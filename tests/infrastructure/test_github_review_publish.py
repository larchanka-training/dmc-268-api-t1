"""Публикация review и check run через `GitHubVcsGateway`."""

import json
from collections.abc import Callable
from typing import Any
from uuid import UUID

import httpx
import pytest

from app.application.ports.vcs_review import (
    InlineCommentDraft,
    MergeGateCheck,
    ReviewThreadState,
)
from app.domain.enums import DiffSide
from app.infrastructure.vcs.errors import VcsUnavailableError
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from tests.infrastructure.conftest import RsaKeyPair

APP_ID = "123456"
INSTALLATION_ID = 1
FAR_FUTURE = "2099-01-01T00:00:00Z"
RUN_ID = UUID("0b6c1f5e-7d1a-4c1e-9a3e-5f0f6b1c2d3e")
MARKER = f"<!-- dmc-268-review-run:{RUN_ID} -->"
BOT = {"login": "dmc-268-review[bot]", "type": "Bot"}
DRAFT = InlineCommentDraft(file_path="a.py", side=DiffSide.NEW, line=3, body="замечание")


def make_gateway(rsa_key_pair: RsaKeyPair, handler: Callable[[httpx.Request], httpx.Response]):
    client = httpx.Client(
        base_url="https://api.github.com",
        transport=httpx.MockTransport(handler),
    )
    auth = GitHubAppAuth(APP_ID, rsa_key_pair.private_pem, client)
    return GitHubVcsGateway(auth, client, sleep=lambda _: None)


def test_publish_pull_request_review_fetches_inline_ids(rsa_key_pair: RsaKeyPair) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        seen.append(request)
        if request.method == "POST" and request.url.path.endswith("/reviews"):
            body = json.loads(request.content.decode())
            assert isinstance(body, dict)
            assert body["event"] == "COMMENT"
            return httpx.Response(
                200,
                json={"id": 9001, "body": body["body"]},
            )
        if request.method == "GET" and "/reviews/9001/comments" in request.url.path:
            return httpx.Response(
                200,
                # Так отвечает GitHub: line и side пустые, есть только position (#265).
                json=[
                    {"id": 41, "path": "a.py", "body": "другое", "line": None, "side": None, "position": 3},
                    {"id": 42, "path": "a.py", "body": "замечание", "line": None, "side": None, "position": 3},
                ],
            )
        return httpx.Response(404)

    gateway = make_gateway(rsa_key_pair, handler)
    publication = gateway.publish_pull_request_review(
        "o/r",
        7,
        "c" * 40,
        INSTALLATION_ID,
        "сводка",
        (DRAFT,),
        review_run_id=RUN_ID,
    )

    assert publication.summary_provider_comment_id == "9001"
    (inline,) = publication.inline_comments
    assert inline is not None
    assert (inline.provider_comment_id, inline.side, inline.line) == ("42", DiffSide.NEW, 3)
    assert any("/pulls/7/reviews/9001/comments" in r.url.path for r in seen)


def test_set_merge_gate_check_posts_check_run(rsa_key_pair: RsaKeyPair) -> None:
    payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        if request.url.path.endswith("/check-runs"):
            payload = json.loads(request.content.decode())
            assert isinstance(payload, dict)
            payloads.append(payload)
            return httpx.Response(201, json={"id": 1})
        return httpx.Response(404)

    gateway = make_gateway(rsa_key_pair, handler)
    gateway.set_merge_gate_check(
        "o/r",
        "c" * 40,
        INSTALLATION_ID,
        MergeGateCheck(conclusion="failure", title="t", summary="s"),
    )

    assert payloads[0]["conclusion"] == "failure"
    assert payloads[0]["name"] == "dmc-268-ai-review"


def test_thread_state_carries_the_current_line_and_none_for_an_outdated_thread(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """Форма ответа - как у живого PR: у outdated-треда `line: null`, `originalLine` - старая строка."""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        payload = json.loads(request.content.decode())
        queries.append(payload["query"])
        nodes = [
            {
                "isResolved": False,
                "isOutdated": False,
                "line": 104,
                "comments": {"nodes": [{"databaseId": 11}]},
            },
            {
                "isResolved": True,
                "isOutdated": True,
                "line": None,
                "comments": {"nodes": [{"databaseId": 22}]},
            },
            {
                "isResolved": False,
                "isOutdated": False,
                "line": 5,
                "comments": {"nodes": [{"databaseId": 33}]},
            },
        ]
        threads = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": nodes}
        return httpx.Response(
            200, json={"data": {"repository": {"pullRequest": {"reviewThreads": threads}}}}
        )

    gateway = make_gateway(rsa_key_pair, handler)
    states = gateway.fetch_review_threads_state("o/r", 7, INSTALLATION_ID, frozenset({"11", "22"}))

    assert states == {
        "11": ReviewThreadState(resolved=False, line=104),
        "22": ReviewThreadState(resolved=True, line=None),
    }
    (query,) = queries
    assert "isOutdated" in query and "line" in query


class _GitHub:
    """Ответы GitHub на публикацию ревью; `create` - что отвечает `POST …/reviews`."""

    def __init__(self, create: Callable[[httpx.Request], httpx.Response]) -> None:
        self.create = create
        self.reviews: list[dict[str, Any]] = []
        self.seen: list[tuple[str, str]] = []
        self.comments_status = 200
        # Статус `GET /app` до первого и после первого `POST …/reviews`.
        self.app_status_before_post = 200
        self.app_status_after_post = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        self.seen.append((request.method, path))
        if path == "/app":
            status = self.app_status_after_post if self.posts() else self.app_status_before_post
            if status != 200:
                return httpx.Response(status, json={"message": "Bad Gateway"})
            return httpx.Response(200, json={"id": 1, "slug": "dmc-268-review"})
        if request.method == "POST" and path == "/repos/o/r/pulls/7/reviews":
            return self.create(request)
        if request.method == "GET" and path == "/repos/o/r/pulls/7/reviews":
            return httpx.Response(200, json=self.reviews)
        if request.method == "GET" and path.endswith("/comments"):
            if self.comments_status != 200:
                return httpx.Response(self.comments_status, json={"message": "Bad Gateway"})
            return httpx.Response(200, json=[{"id": 42, "path": "a.py", "body": "замечание", "line": None, "side": None}])
        return httpx.Response(404)

    def posts(self) -> int:
        return self.seen.count(("POST", "/repos/o/r/pulls/7/reviews"))


def _publish(gateway: GitHubVcsGateway) -> Any:
    return gateway.publish_pull_request_review(
        "o/r", 7, "c" * 40, INSTALLATION_ID, "сводка", (DRAFT,), review_run_id=RUN_ID
    )


def test_review_body_carries_the_hidden_run_marker(rsa_key_pair: RsaKeyPair) -> None:
    bodies: list[str] = []

    def create(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content)["body"])
        return httpx.Response(200, json={"id": 9001, "body": bodies[-1]})

    github = _GitHub(create)
    _publish(make_gateway(rsa_key_pair, github.handler))

    (body,) = bodies
    assert body.startswith("сводка")
    assert body.endswith(MARKER)


def test_502_on_create_review_is_not_repeated_and_the_review_is_found_by_marker(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """#185: GitHub создал ревью, клиент получил 502 - второго POST нет, ревью - по маркеру от бота."""
    github = _GitHub(lambda request: httpx.Response(502, json={"message": "Bad Gateway"}))
    github.reviews = [
        {"id": 1, "body": f"копия {MARKER}", "user": {"login": "mallory", "type": "User"}},
        {"id": 2, "body": MARKER, "user": {"login": "other-app[bot]", "type": "Bot"}},
        {"id": 9001, "body": f"сводка\n\n{MARKER}", "user": BOT},
    ]

    publication = _publish(make_gateway(rsa_key_pair, github.handler))

    assert github.posts() == 1
    assert publication.summary_provider_comment_id == "9001"
    assert publication.inline_comments[0].provider_comment_id == "42"


def test_bot_login_is_known_before_the_post_so_a_later_outage_does_not_hide_the_review(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """POST ответил 502, и `GET /app` после него тоже 502: логин бота взят до POST, ревью найдено."""
    github = _GitHub(lambda request: httpx.Response(502, json={"message": "Bad Gateway"}))
    github.reviews = [{"id": 9001, "body": f"сводка\n\n{MARKER}", "user": BOT}]
    github.app_status_after_post = 502

    publication = _publish(make_gateway(rsa_key_pair, github.handler))

    assert github.posts() == 1
    assert publication.summary_provider_comment_id == "9001"


def test_failed_bot_login_before_the_post_does_not_stop_the_publication(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """Логин бота заранее - best-effort: `GET /app` упал, ревью всё равно публикуется."""
    github = _GitHub(lambda request: httpx.Response(200, json={"id": 9001}))
    github.app_status_before_post = 502

    publication = _publish(make_gateway(rsa_key_pair, github.handler))

    assert github.posts() == 1
    assert publication.summary_provider_comment_id == "9001"


def test_timeout_on_create_review_looks_the_review_up_instead_of_repeating(
    rsa_key_pair: RsaKeyPair,
) -> None:
    def create(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    github = _GitHub(create)
    github.reviews = [{"id": 9001, "body": f"сводка\n\n{MARKER}", "user": BOT}]

    publication = _publish(make_gateway(rsa_key_pair, github.handler))

    assert github.posts() == 1
    assert publication.summary_provider_comment_id == "9001"


def _sleeping_gateway(
    rsa_key_pair: RsaKeyPair, handler: Callable[[httpx.Request], httpx.Response], sleeps: list[float]
) -> GitHubVcsGateway:
    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    auth = GitHubAppAuth(APP_ID, rsa_key_pair.private_pem, client)
    return GitHubVcsGateway(auth, client, sleep=sleeps.append, clock=lambda: 1_800_000_000.0)


def test_rate_limited_create_review_is_posted_again_after_the_pause_and_a_lookup(
    rsa_key_pair: RsaKeyPair,
) -> None:
    """#185: отказ лимита - пауза по `Retry-After`, ревью по маркеру нет, второй POST создаёт ревью."""
    responses = [
        httpx.Response(429, headers={"Retry-After": "7"}, json={"message": "secondary rate limit"}),
        httpx.Response(200, json={"id": 9001, "body": "x"}),
    ]
    github = _GitHub(lambda request: responses.pop(0))
    sleeps: list[float] = []

    publication = _publish(_sleeping_gateway(rsa_key_pair, github.handler, sleeps))

    assert github.posts() == 2
    assert sleeps == [7.0]
    # Поиск - между паузой и повтором.
    lookup = github.seen.index(("GET", "/repos/o/r/pulls/7/reviews"))
    assert github.seen.index(("POST", "/repos/o/r/pulls/7/reviews")) < lookup
    assert publication.summary_provider_comment_id == "9001"


def test_review_written_behind_a_502_is_found_after_the_pause_without_a_second_post(
    rsa_key_pair: RsaKeyPair,
) -> None:
    github = _GitHub(lambda request: httpx.Response(502, json={"message": "Bad Gateway"}))
    github.reviews = [{"id": 9001, "body": f"сводка\n\n{MARKER}", "user": BOT}]
    sleeps: list[float] = []

    publication = _publish(_sleeping_gateway(rsa_key_pair, github.handler, sleeps))

    assert github.posts() == 1
    assert sleeps == [0.5]
    assert publication.summary_provider_comment_id == "9001"


@pytest.mark.parametrize("status", [502, 429])
def test_create_review_gives_up_after_max_attempts_without_our_review(
    rsa_key_pair: RsaKeyPair, status: int
) -> None:
    """Ревью с маркером нет ни после одной попытки: POST ровно `max_attempts` раз, затем сбой.
    Ревью человека с тем же маркером своим не считается."""
    github = _GitHub(lambda request: httpx.Response(status, headers={"Retry-After": "1"}, json={"message": "err"}))
    github.reviews = [{"id": 1, "body": MARKER, "user": {"login": "mallory", "type": "User"}}]

    with pytest.raises(VcsUnavailableError):
        _publish(_sleeping_gateway(rsa_key_pair, github.handler, []))
    assert github.posts() == 3


def test_lost_inline_comment_list_keeps_the_review_id(rsa_key_pair: RsaKeyPair) -> None:
    """#185: `GET …/reviews/{id}/comments` упал - ревью опубликовано, id сводки есть, inline - неизвестны."""
    github = _GitHub(lambda request: httpx.Response(200, json={"id": 9001, "body": "x"}))
    github.comments_status = 502

    publication = _publish(make_gateway(rsa_key_pair, github.handler))

    assert publication.summary_provider_comment_id == "9001"
    assert publication.inline_comments == (None,)


def test_check_run_is_repeated_on_502(rsa_key_pair: RsaKeyPair) -> None:
    """Check run с тем же именем на тот же коммит заменяет прежний: повтор безвреден."""
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        posts.append(request.url.path)
        if len(posts) == 1:
            return httpx.Response(502, json={"message": "Bad Gateway"})
        return httpx.Response(201, json={"id": 1})

    make_gateway(rsa_key_pair, handler).set_merge_gate_check(
        "o/r", "c" * 40, INSTALLATION_ID, MergeGateCheck(conclusion="success", title="t", summary="s")
    )
    assert len(posts) == 2


def test_graphql_rate_limited_with_http_200_is_retried(rsa_key_pair: RsaKeyPair) -> None:
    """#185: GraphQL отдаёт превышение лимита со статусом 200 и `errors[].type = RATE_LIMITED`."""
    sleeps: list[float] = []
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(
                200,
                headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(1_800_000_000 + 9)},
                json={"errors": [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}]},
            )
        threads = {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": []}
        return httpx.Response(
            200, json={"data": {"repository": {"pullRequest": {"reviewThreads": threads}}}}
        )

    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handler))
    auth = GitHubAppAuth(APP_ID, rsa_key_pair.private_pem, client)
    gateway = GitHubVcsGateway(auth, client, sleep=sleeps.append, clock=lambda: 1_800_000_000.0)

    assert gateway.fetch_review_threads_state("o/r", 7, INSTALLATION_ID, frozenset({"11"})) == {}
    assert len(calls) == 2
    assert sleeps == [10.0]
