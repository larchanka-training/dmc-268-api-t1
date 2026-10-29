"""Общий PR и ответы модели сценариев chaos-сьюта."""

import httpx2
from fastapi.testclient import TestClient

from app.domain.enums import ReviewRunStage, ReviewRunStepStatus

from ..e2e.stack import Stack, deliver
from ..fakes.github_api import PullRequest
from ..fakes.llm_gateway import finding_json, review_response, verification_response

BASE = "a0" * 20
HEAD = "a1" * 20
PR = 21
CODE_PATH = "app/payments.py"
CODE = """\
def charge(amount, balance):
    if amount > balance:
        raise ValueError("недостаточно средств")
    return balance - amount
"""
RAISE_LINE = 3

REVIEW = review_response(
    finding_json(
        CODE_PATH, RAISE_LINE, "Отказ бросается исключением.", category="correctness", severity="critical"
    ),
    tokens=40,
)
VERIFIED = verification_response(("F1", "keep"), tokens=10)


class WorkerDied(BaseException):
    """Смерть процесса воркера посреди обработки (OOM, SIGKILL, выкат).

    `BaseException`, а не `Exception`: consume-цикл ловит только `Exception` и
    nack'ает в DLQ; это исключение он не ловит - сообщение остаётся
    неподтверждённым, и брокер возвращает его в очередь при закрытии
    соединения, как при обрыве TCP умершего процесса.
    """


def open_pull(stack: Stack) -> PullRequest:
    return stack.github.open_pull(PR, base_sha=BASE, head_sha=HEAD, head_files={CODE_PATH: CODE})


def accept(stack: Stack, pull: PullRequest, client: httpx2.Client | None = None) -> str:
    """Подписанный вебхук принят: id прогона в очереди."""
    response = deliver(client or TestClient(stack.api()), pull)
    assert response.status_code == 202, response.text
    run_id: str = response.json()["id"]
    return run_id


def step_trail(stack: Stack, run_id: str) -> list[tuple[ReviewRunStage, ReviewRunStepStatus, str | None]]:
    """Шаги прогона по порядку: этап, исход, код сбоя."""
    steps = sorted(stack.steps(run_id), key=lambda s: s.started_at)
    return [(s.stage, s.status, s.error_code) for s in steps]
