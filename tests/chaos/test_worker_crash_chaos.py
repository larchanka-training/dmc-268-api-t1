"""Смерть воркера посреди прогона: сообщение не теряется, PR не получает дублей.

Смерть - `WorkerDied` (`BaseException`) в нужной точке: в вызове модели, в
запросе к GitHub, уже выполненном на стороне GitHub. Consume-цикл его не
ловит, сообщение не подтверждено, и брокер возвращает его в очередь при
закрытии соединения.

По спеке `job-queue` («Прогон захватывает один воркер») повторная доставка чужой
прогон не трогает и подтверждается: живой он или брошенный, отсюда не отличить.
Брошенный переводит в `failed` очистка: шаг прошлой попытки - `REVIEW_RUN_STALE`.
Ревью в PR после этого даёт новый прогон: `failed` повтор вебхука не блокирует
(`webhook-intake`). Сценарии проверяют всю цепочку: смерть → повторная доставка →
очистка → новый прогон, и что в PR одно ревью на прогон без дублей замечаний.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.application.stale_sweep import sweep_stale_runs
from app.domain.enums import ReviewRunStage, ReviewRunStatus, ReviewRunStepStatus
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.queue.rabbitmq import DLQ_NAME, QUEUE_NAME

from ..conftest import requires_broker, requires_db
from ..e2e.stack import Stack, deliver
from ..fakes.github_api import REPO_FULL_NAME, NetworkFault, PullRequest
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
    """Второй воркер получает то же сообщение: прогон не тронут, сообщение подтверждено.
    Очистка затем переводит брошенный прогон в `failed`, его шаг - `STALE`."""
    model = _model()
    in_work = stack.run(run_id).status
    stack.work(model, 1)
    assert model.requests == [], "повторная доставка снова вызвала модель"
    assert stack.run(run_id).status is in_work
    assert stack.queue_depth(QUEUE_NAME) == 0
    assert stack.queue_depth(DLQ_NAME) == 0

    moment = datetime.now(UTC)
    swept = sweep_stale_runs(
        SqlAlchemyUnitOfWork(stack.engine), now=moment, limit=timedelta(0), queued_limit=timedelta(0)
    )
    assert swept == 1
    assert stack.run(run_id).status is ReviewRunStatus.FAILED
    trail = step_trail(stack, run_id)
    assert (stage, ReviewRunStepStatus.FAILED, STALE) in trail
    assert ReviewRunStepStatus.RUNNING not in {status for _, status, _ in trail}


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
    # «Идёт ревью» умершего прогона и нового, затем итог нового.
    assert [(c.head_sha, c.conclusion) for c in stack.github.check_runs] == [
        (HEAD, None),
        (HEAD, None),
        (HEAD, "failure"),
    ]


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
    # Первый check run - «Идёт ревью» захвата; смерть - на итоговом.
    stack.github.network_faults = [
        NetworkFault("POST", rf"/repos/{REPO_FULL_NAME}/check-runs", WorkerDied(), passes=1)
    ]

    _die(stack, _model())
    assert len(stack.github.reviews_of(PR)) == 1
    assert [c.status for c in stack.github.check_runs] == ["in_progress"]
    _redeliver_to_next_worker(stack, run_id, ReviewRunStage.PUBLISHING)

    _recover_with_new_delivery(stack, pull, run_id)

    comments = _inline_comments(stack)
    assert len(comments) == len(set(comments)), f"дубль замечания: {comments}"
    # «Идёт ревью» умершего прогона и нового, затем итог нового.
    assert [(c.head_sha, c.conclusion) for c in stack.github.check_runs] == [
        (HEAD, None),
        (HEAD, None),
        (HEAD, "failure"),
    ]
