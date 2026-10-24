"""Повторный прогон на том же PR: замечание с тредом бота не публикуется второй раз (#148).

Подписанный вебхук → API → база → очередь → воркер → модель → GitHub, два push
подряд. Модель на обоих прогонах находит одну и ту же проблему; GitHub -
`FakeGitHub` под настоящим адаптером, треды и их строки - из его GraphQL.
"""

import pytest
from fastapi.testclient import TestClient

from app.domain.enums import ReviewRunStatus
from app.infrastructure.queue.rabbitmq import DLQ_NAME

from ..conftest import requires_broker, requires_db
from ..fakes.github_api import PullRequest
from ..fakes.llm_gateway import (
    ScriptedLlmGateway,
    finding_json,
    review_response,
    verification_response,
)
from .stack import Stack, deliver

pytestmark = [pytest.mark.integration, requires_db, requires_broker]

BASE = "b0" * 20
FIRST_HEAD = "c1" * 20
SECOND_HEAD = "d2" * 20
PR = 9
CODE_PATH = "app/payments.py"
CODE = """\
def charge(amount, balance):
    if amount > balance:
        raise ValueError("недостаточно средств")
    return balance - amount
"""
RAISE_LINE = 3


def _push(stack: Stack, head_sha: str) -> PullRequest:
    return stack.github.open_pull(
        PR, base_sha=BASE, head_sha=head_sha, head_files={CODE_PATH: CODE}
    )


def _model(severity: str) -> ScriptedLlmGateway:
    return ScriptedLlmGateway(
        [
            review_response(
                finding_json(
                    CODE_PATH,
                    RAISE_LINE,
                    "Отказ бросается исключением.",
                    category="correctness",
                    severity=severity,
                )
            ),
            verification_response(("F1", "keep")),
        ]
    )


def _review(stack: Stack, pull: PullRequest, *, action: str, severity: str = "critical") -> str:
    response = deliver(TestClient(stack.api()), pull, action=action)
    assert response.status_code == 202, response.text
    run_id: str = response.json()["id"]
    stack.work(_model(severity), 1)
    assert stack.run(run_id).status is ReviewRunStatus.COMPLETED
    return run_id


def _first_thread(stack: Stack) -> str:
    (first,) = stack.github.reviews_of(PR)
    (comment,) = first.comments
    return f"T_{comment.id}"


def test_second_push_with_the_same_finding_adds_no_inline_comment(stack: Stack) -> None:
    first_run = _review(stack, _push(stack, FIRST_HEAD), action="opened")
    _first_thread(stack)

    second_run = _review(stack, _push(stack, SECOND_HEAD), action="synchronize")

    _, second = stack.github.reviews_of(PR)
    assert second.comments == ()
    (covered,) = stack.findings(second_run)
    (original,) = stack.findings(first_run)
    assert covered.covered_by_finding_id == original.id
    # Тред с critical открыт - мердж по-прежнему заблокирован.
    assert [(c.head_sha, c.conclusion) for c in stack.github.check_runs] == [
        (FIRST_HEAD, "failure"),
        (SECOND_HEAD, "failure"),
    ]


def test_thread_resolved_by_a_human_is_not_repeated(stack: Stack) -> None:
    """Тред закрыли на GitHub руками, вебхука о закрытии не было - сверка видит закрытие."""
    _review(stack, _push(stack, FIRST_HEAD), action="opened")
    stack.github.resolved_threads.add(_first_thread(stack))

    _review(stack, _push(stack, SECOND_HEAD), action="synchronize")

    _, second = stack.github.reviews_of(PR)
    assert second.comments == ()
    assert stack.github.check_runs[-1].conclusion == "success"


def test_outdated_thread_does_not_hide_the_finding(stack: Stack) -> None:
    _review(stack, _push(stack, FIRST_HEAD), action="opened")
    stack.github.outdated_threads.add(_first_thread(stack))

    _review(stack, _push(stack, SECOND_HEAD), action="synchronize")

    _, second = stack.github.reviews_of(PR)
    (comment,) = second.comments
    assert (comment.path, comment.line) == (CODE_PATH, RAISE_LINE)


def test_graphql_outage_on_a_rerun_publishes_the_finding_and_the_gate_from_the_database(
    stack: Stack,
) -> None:
    """#76, отчёт §5.3: GraphQL отвечает 502 на сверке тредов - прогон `completed`, gate по базе.

    Без ответа GitHub текущая строка треда неизвестна: замечание публикуется,
    повтор лучше потерянного critical.
    """
    _review(stack, _push(stack, FIRST_HEAD), action="opened")
    stack.github.graphql_status = 502

    _review(stack, _push(stack, SECOND_HEAD), action="synchronize")

    # Сверка тредов дошла до GraphQL и исчерпала попытки адаптера.
    assert stack.github.requests.count(("POST", "/graphql")) == 3
    _, second = stack.github.reviews_of(PR)
    assert len(second.comments) == 1
    assert stack.github.check_runs[-1].conclusion == "failure"
    assert stack.queue_depth(DLQ_NAME) == 0
