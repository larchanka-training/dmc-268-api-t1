"""Публикация review и check run через `GitHubVcsGateway`."""

import json
from collections.abc import Callable
from typing import Any

import httpx

from app.application.ports.vcs_review import InlineCommentDraft, MergeGateCheck
from app.domain.enums import DiffSide
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from tests.infrastructure.conftest import RsaKeyPair

APP_ID = "123456"
INSTALLATION_ID = 1
FAR_FUTURE = "2099-01-01T00:00:00Z"


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
                json=[
                    {
                        "id": 42,
                        "path": "a.py",
                        "line": 3,
                        "side": "RIGHT",
                    }
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
        (
            InlineCommentDraft(
                file_path="a.py", side=DiffSide.NEW, line=3, body="замечание"
            ),
        ),
    )

    assert publication.summary_provider_comment_id == "9001"
    assert publication.inline_comments[0].provider_comment_id == "42"
    assert any("/pulls/7/reviews/9001/comments" in r.url.path for r in seen)


def test_reply_uses_pull_number_in_path(rsa_key_pair: RsaKeyPair) -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/app/installations/"):
            return httpx.Response(200, json={"token": "ghs_test", "expires_at": FAR_FUTURE})
        paths.append(request.url.path)
        if request.method == "POST" and "/pulls/7/comments/99/replies" in request.url.path:
            return httpx.Response(200, json={"id": 100})
        return httpx.Response(404)

    gateway = make_gateway(rsa_key_pair, handler)
    assert gateway.reply_to_review_comment("o/r", 7, INSTALLATION_ID, "99", "ответ") == "100"
    assert any("/pulls/7/comments/99/replies" in p for p in paths)


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
