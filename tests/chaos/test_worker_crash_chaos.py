"""Смерть воркера посреди прогона: сообщение не теряется, PR не получает дублей.

Смерть - `WorkerDied` (`BaseException`) в нужной точке: в вызове модели, в
запросе к GitHub, уже выполненном на стороне GitHub. Consume-цикл его не
ловит, сообщение не подтверждено, и брокер возвращает его в очередь при
закрытии соединения.

По спеке `review-pipeline` («Повторная доставка незавершённого прогона») прогон
захватывает ровно один воркер, поэтому повторная доставка недоведённый прогон
не продолжает, а переводит в `failed`: шаг прошлой попытки - `REVIEW_RUN_STALE`,
сообщение - в DLQ. Ревью в PR после этого даёт новый прогон: `failed`
повтор вебхука не блокирует (`webhook-intake`). Сценарии проверяют всю цепочку:
смерть → повторная доставка → новый прогон, и что в PR одно ревью на прогон без
дублей замечаний.
"""

import pytest
from fastapi.testclient import TestClient

from app.domain.enums import ReviewRunStage, ReviewRunStatus, ReviewRunStepStatus
from app.infrastructure.queue.rabbitmq import DLQ_NAME, QUEUE_NAME

from ..conftest import requires_broker, requires_db
from ..e2e.stack import REPO_FULL_NAME, Stack, deliver
from ..fakes.github_api import NetworkFault, PullRequest
from ..fakes.llm_gateway import ScriptedLlmGateway
from ..fakes.llm_provider import FakeLlmProvider
from .scenario import (
    HEAD,
    PR,
    REVIEW,
    VERIFIED,
    WorkerDied,
    accept,
    open_pull,
    step_trail,
)

pytestmark = [pytest.mark.integration, pytest.mark.chaos, requires_db, requires_broker]

CREATE_REVIEW = ("POST", f"/repos/{REPO_FULL_NAME}/pulls/{PR}/reviews")
STALE = "REVIEW_RUN_STALE"


def _model() -> ScriptedLlmGateway:
    return ScriptedLlmGateway([REVIEW, VERIFIED])


def _die(stack: Stack, llm: object) -> None:
    with pytest.raises(WorkerDied):
        stack.work(llm, 1)  # type: ignore[arg-type]
    assert stack.queue_depth(QUEUE_NAME) == 1, "сообщение умершего воркера потеряно"


def _redeliver_to_next_worker(stack: Stack, run_id: str, stage: ReviewRunStage) -> None:
    """Второй воркер получает то же сообщение: прогон `failed`, брошенный шаг - `STALE`, без новой работы."""
    model = _model()
    stack.work(model, 1)
    assert model.requests == [], "повторная доставка снова вызвала модель"
    assert stack.run(run_id).status is ReviewRunStatus.FAILED
    trail = step_trail(stack, run_id)
    assert (stage, ReviewRunStepStatus.FAILED, STALE) in trail
    assert ReviewRunStepStatus.RUNNING not in {status for _, status, _ in trail}
    assert stack.queue_depth(QUEUE_NAME) == 0
    assert stack.queue_depth(DLQ_NAME) == 1
    stack.purge_queues()


def _recover_with_new_delivery(stack: Stack, pull: PullRequest, failed_run: str) -> str:
    """Новая доставка вебхука (push, ручной перезапуск): новый прогон доводит ревью."""
    response = deliver(TestClient(stack.api()), pull, delivery="delivery-after-crash")
    assert response.status_code == 202, response.text
    run_id: str = response.json()["id"]
    assert run_id != failed_run
    stack.work(_model(), 1)
    assert stack.run(run_id).status is ReviewRunStatus.COMPLETED
    assert stack.queue_depth(DLQ_NAME) == 0
    return run_id


def _inline_comments(stack: Stack) -> list[tuple[str, int]]:
    return [(c.path, c.line) for r in stack.github.reviews_of(PR) for c in r.comments]


def test_worker_dies_during_the_model_call(stack: Stack) -> None:
    """Смерть в вызове модели: в PR ничего; новый прогон публикует одно ревью."""
    pull = open_pull(stack)
    run_id = accept(stack, pull)

    _die(stack, FakeLlmProvider([WorkerDied()]).gateway())
    assert stack.run(run_id).status is ReviewRunStatus.ANALYSING
    _redeliver_to_next_worker(stack, run_id, ReviewRunStage.ANALYSING)
    assert stack.github.reviews_of(PR) == []

    _recover_with_new_delivery(stack, pull, run_id)

    assert len(stack.github.reviews_of(PR)) == 1
    assert len(_inline_comments(stack)) == 1
    assert [(c.head_sha, c.conclusion) for c in stack.github.check_runs] == [(HEAD, "failure")]


def test_worker_dies_after_github_stored_the_review(stack: Stack) -> None:
    """Смерть после записи ревью на GitHub: повторная доставка его не постит снова,
    новый прогон не дублирует замечание к той же строке."""
    pull = open_pull(stack)
    run_id = accept(stack, pull)
    stack.github.network_faults = [
        NetworkFault("POST", rf"/repos/{REPO_FULL_NAME}/pulls/{PR}/reviews", WorkerDied(), stored=True)
    ]

    _die(stack, _model())
    assert len(stack.github.reviews_of(PR)) == 1
    _redeliver_to_next_worker(stack, run_id, ReviewRunStage.PUBLISHING)
    assert stack.github.requests.count(CREATE_REVIEW) == 1

    _recover_with_new_delivery(stack, pull, run_id)

    comments = _inline_comments(stack)
    assert len(comments) == len(set(comments)), f"дубль замечания: {comments}"
    assert stack.github.check_runs[-1].conclusion == "failure"


def test_worker_dies_while_setting_the_merge_gate(stack: Stack) -> None:
    """Смерть на check run после публикации ревью: gate не выставлен, пока новый прогон
    его не выставит; замечание не дублируется."""
    pull = open_pull(stack)
    run_id = accept(stack, pull)
    stack.github.network_faults = [NetworkFault("POST", rf"/repos/{REPO_FULL_NAME}/check-runs", WorkerDied())]

    _die(stack, _model())
    assert len(stack.github.reviews_of(PR)) == 1
    assert stack.github.check_runs == []
    _redeliver_to_next_worker(stack, run_id, ReviewRunStage.PUBLISHING)

    _recover_with_new_delivery(stack, pull, run_id)

    comments = _inline_comments(stack)
    assert len(comments) == len(set(comments)), f"дубль замечания: {comments}"
    assert [(c.head_sha, c.conclusion) for c in stack.github.check_runs] == [(HEAD, "failure")]
