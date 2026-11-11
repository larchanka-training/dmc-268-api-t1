"""Долгий отказ провайдера модели за breaker'ом (#294).

Вебхук → API → база → очередь → воркер → `FallbackLlmGateway` с breaker'ом →
`OpenAiCompatibleGateway` → HTTP. Провайдер отвечает `503`: первый прогон падает
и размыкает breaker, следующий откладывается в очереди — больше раз, чем
`DELIVERY_LIMIT`, — не тратя ни попыток доставки, ни вызовов модели. Провайдер
вернулся, пауза вышла — тот же прогон идёт пробным вызовом и публикуется.
Часы breaker'а — сценария: пауза проходит без ожидания.
"""

import pytest

from app.application.circuit_breaker import CircuitBreaker, deferral
from app.domain.circuit_breaker import BreakerPolicy, BreakerState
from app.domain.enums import ReviewRunStatus
from app.infrastructure.llm.fallback import FallbackLlmGateway
from app.infrastructure.queue.rabbitmq import DELIVERY_LIMIT, DLQ_NAME, QUEUE_NAME

from ..conftest import requires_broker, requires_db
from ..e2e.stack import Stack
from ..fakes.llm_provider import FakeLlmProvider
from .scenario import BASE, CODE, CODE_PATH, PR, REVIEW, VERIFIED, accept, open_pull

pytestmark = [pytest.mark.integration, pytest.mark.chaos, requires_db, requires_broker]

PAUSE = 60.0
# Второй PR: прогон на том же PR отменил бы первый (`supersede`).
SECOND_PR = 22
SECOND_HEAD = "b1" * 20


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_long_outage_defers_runs_without_spending_attempts_and_they_go_after_recovery(
    stack: Stack,
) -> None:
    clock = Clock()
    breaker = CircuitBreaker(
        "Eurorouter",
        BreakerPolicy(failure_threshold=1, pause_seconds=PAUSE, max_pause_seconds=10 * PAUSE),
        clock=clock,
    )
    provider = FakeLlmProvider([503])
    gateway = FallbackLlmGateway([("Eurorouter", provider.gateway())], breakers={"Eurorouter": breaker})

    def paused_for() -> float | None:
        return deferral(None, [breaker])

    # Сбой провайдера: прогон падает как раньше, breaker размыкается.
    failed_id = accept(stack, open_pull(stack))
    stack.work(gateway, 1, paused_for=paused_for)
    assert stack.run(failed_id).status is ReviewRunStatus.FAILED
    assert breaker.state() is BreakerState.OPEN

    # Отказ длится: следующий прогон откладывается снова и снова — дольше лимита доставок.
    second = stack.github.open_pull(SECOND_PR, base_sha=BASE, head_sha=SECOND_HEAD, head_files={CODE_PATH: CODE})
    waiting_id = accept(stack, second)
    stack.work(gateway, DELIVERY_LIMIT + 2, paused_for=paused_for)

    assert stack.run(waiting_id).status is ReviewRunStatus.QUEUED
    assert stack.steps(waiting_id) == []
    assert provider.calls == 1  # модель не вызывалась: попытки не сгорели
    assert stack.queue_depth(QUEUE_NAME) == 1  # сообщение ждёт в очереди,
    assert stack.queue_depth(DLQ_NAME) == 1  # а в DLQ — только упавший до размыкания

    # Провайдер вернулся, пауза вышла: тот же прогон идёт пробным вызовом и доходит до конца.
    provider.answers.extend([REVIEW, VERIFIED])
    clock.now += PAUSE
    stack.work(gateway, 1, paused_for=paused_for)

    assert stack.run(waiting_id).status is ReviewRunStatus.COMPLETED
    assert breaker.state() is BreakerState.CLOSED
    (review,) = stack.github.reviews_of(SECOND_PR)
    assert len(review.comments) == 1
    assert stack.github.reviews_of(PR) == []
    assert stack.queue_depth(QUEUE_NAME) == 0
    assert stack.queue_depth(DLQ_NAME) == 1
