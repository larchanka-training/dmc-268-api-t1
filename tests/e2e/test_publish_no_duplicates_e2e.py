"""Публикация ревью без дублей и без потери `critical` при сбоях GitHub (#185).

Подписанный вебхук → API → база → очередь → воркер → модель → GitHub. GitHub -
`FakeGitHub` под настоящим адаптером: ревью записано, а клиенту ушёл 502;
список комментариев ревью не отдаётся.
"""

import pytest
from fastapi.testclient import TestClient

from app.domain.enums import CommentKind, ReviewRunStatus
from app.infrastructure.queue.rabbitmq import DLQ_NAME

from ..conftest import requires_broker, requires_db
from ..fakes.github_api import REPO_FULL_NAME, CreateReviewFailure, PullRequest
from ..fakes.llm_gateway import (
    ScriptedLlmGateway,
    finding_json,
    review_response,
    verification_response,
)
from .stack import Stack, deliver

pytestmark = [pytest.mark.integration, requires_db, requires_broker]

BASE = "b0" * 20
HEAD = "c1" * 20
PR = 11
CODE_PATH = "app/payments.py"
CODE = """\
def charge(amount, balance):
    if amount > balance:
        raise ValueError("недостаточно средств")
    return balance - amount
"""
RAISE_LINE = 3
CREATE_REVIEW = ("POST", f"/repos/{REPO_FULL_NAME}/pulls/{PR}/reviews")


def _pull(stack: Stack) -> PullRequest:
    return stack.github.open_pull(PR, base_sha=BASE, head_sha=HEAD, head_files={CODE_PATH: CODE})


def _critical_model() -> ScriptedLlmGateway:
    return ScriptedLlmGateway(
        [
            review_response(
                finding_json(
                    CODE_PATH,
                    RAISE_LINE,
                    "Отказ бросается исключением.",
                    category="correctness",
                    severity="critical",
                )
            ),
            verification_response(("F1", "keep")),
        ]
    )


def _review(stack: Stack, pull: PullRequest) -> str:
    response = deliver(TestClient(stack.api()), pull)
    assert response.status_code == 202, response.text
    run_id: str = response.json()["id"]
    stack.work(_critical_model(), 1)
    return run_id


def test_review_created_behind_a_502_is_not_published_twice(stack: Stack) -> None:
    """`POST /reviews` записал ревью и ответил 502: повтора нет, ревью найдено по маркеру."""
    stack.github.create_review_failures = [CreateReviewFailure(502, stored=True)]

    run_id = _review(stack, _pull(stack))

    assert stack.run(run_id).status is ReviewRunStatus.COMPLETED
    assert stack.github.requests.count(CREATE_REVIEW) == 1
    (review,) = stack.github.reviews_of(PR)
    assert f"<!-- dmc-268-review-run:{run_id} -->" in review.body
    (comment,) = review.comments
    kinds = {(p.kind, p.provider_comment_id) for p in stack.published(run_id)}
    assert kinds == {(CommentKind.SUMMARY, str(review.id)), (CommentKind.INLINE, str(comment.id))}
    assert [(c.head_sha, c.conclusion) for c in stack.github.check_runs] == [(HEAD, "failure")]
    assert stack.queue_depth(DLQ_NAME) == 0


def test_a_marker_in_a_foreign_review_is_not_taken_for_ours(stack: Stack) -> None:
    """Ревью с нашим маркером, но не от бота App, опубликованным не считается."""
    pull = _pull(stack)
    response = deliver(TestClient(stack.api()), pull)
    run_id = response.json()["id"]
    marker = f"<!-- dmc-268-review-run:{run_id} -->"
    stack.github.add_foreign_review(PR, marker, login="mallory", user_type="User")
    stack.github.add_foreign_review(PR, marker, login="other-app[bot]", user_type="Bot")
    stack.github.create_review_failures = [CreateReviewFailure(502, stored=False)] * 3

    stack.work(_critical_model(), 1)

    # Своего ревью по маркеру нет ни после одной попытки: POST повторяется до
    # предела попыток, чужое ревью своим не считается, прогон падает.
    assert stack.run(run_id).status is ReviewRunStatus.FAILED
    assert stack.github.requests.count(CREATE_REVIEW) == 3
    assert stack.published(run_id) == []
    assert [r.user_login for r in stack.github.reviews_of(PR)] == ["mallory", "other-app[bot]"]


def test_rate_limited_create_review_is_published_after_the_pause(stack: Stack) -> None:
    """Отказ лимита до записи: пауза, поиск по маркеру ничего не нашёл, второй POST - одно ревью."""
    stack.github.create_review_failures = [
        CreateReviewFailure(
            429, stored=False, headers={"Retry-After": "7"}, message="secondary rate limit"
        )
    ]

    run_id = _review(stack, _pull(stack))

    assert stack.run(run_id).status is ReviewRunStatus.COMPLETED
    assert stack.github.requests.count(CREATE_REVIEW) == 2
    (review,) = stack.github.reviews_of(PR)
    assert [(c.head_sha, c.conclusion) for c in stack.github.check_runs] == [(HEAD, "failure")]
    assert {p.provider_comment_id for p in stack.published(run_id)} >= {str(review.id)}
    assert stack.queue_depth(DLQ_NAME) == 0


def test_lost_inline_ids_keep_the_summary_and_the_gate_sees_the_critical(stack: Stack) -> None:
    """`GET …/reviews/{id}/comments` упал: сводка записана, прогон `completed`, gate - `failure`."""
    stack.github.review_comments_status = 502

    run_id = _review(stack, _pull(stack))

    assert stack.run(run_id).status is ReviewRunStatus.COMPLETED
    assert stack.github.requests.count(CREATE_REVIEW) == 1
    (review,) = stack.github.reviews_of(PR)
    assert len(review.comments) == 1
    assert [(p.kind, p.provider_comment_id) for p in stack.published(run_id)] == [
        (CommentKind.SUMMARY, str(review.id))
    ]
    assert [(c.head_sha, c.conclusion) for c in stack.github.check_runs] == [(HEAD, "failure")]
    assert stack.queue_depth(DLQ_NAME) == 0
