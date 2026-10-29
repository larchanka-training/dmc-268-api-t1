"""Сбои модели: таймаут, `429`, обрыв соединения - через настоящий адаптер провайдера.

Вебхук → API → база → очередь → воркер → `OpenAiCompatibleGateway` → HTTP.
Ответы провайдера - `FakeLlmProvider` за `httpx.MockTransport`; GitHub - `FakeGitHub`.
"""

import httpx
import pytest

from app.domain.enums import (
    ReviewRunStage,
    ReviewRunStatus,
    ReviewRunStepStatus,
    VerificationOutcome,
)
from app.infrastructure.llm.fallback import FallbackLlmGateway
from app.infrastructure.queue.rabbitmq import DLQ_NAME, QUEUE_NAME

from ..conftest import requires_broker, requires_db
from ..e2e.stack import Stack
from ..fakes.llm_provider import FakeLlmProvider
from .scenario import PR, REVIEW, VERIFIED, accept, open_pull, step_trail

pytestmark = [pytest.mark.integration, pytest.mark.chaos, requires_db, requires_broker]

ANALYSING, VERIFYING = ReviewRunStage.ANALYSING, ReviewRunStage.VERIFYING
DONE, FAILED = ReviewRunStepStatus.COMPLETED, ReviewRunStepStatus.FAILED


def _assert_failed_into_dlq(stack: Stack, run_id: str, code: str) -> None:
    """Прогон `failed` с кодом на шаге `analysing`, сообщение - в DLQ, в PR ничего."""
    run = stack.run(run_id)
    assert run.status is ReviewRunStatus.FAILED
    assert (ANALYSING, FAILED, code) in step_trail(stack, run_id)
    assert all(status is not ReviewRunStepStatus.RUNNING for _, status, _ in step_trail(stack, run_id))
    assert stack.queue_depth(QUEUE_NAME) == 0
    assert stack.queue_depth(DLQ_NAME) == 1
    assert stack.github.reviews_of(PR) == []
    assert stack.published(run_id) == []
    # Ни `success`, ни иного check run: непроверенный PR не выглядит проверенным.
    assert stack.github.check_runs == []


def test_review_call_timeout_fails_the_run_with_llm_timeout(stack: Stack) -> None:
    """Провайдер не ответил за таймаут чтения: `LLM_TIMEOUT`, без повтора и без публикации."""
    provider = FakeLlmProvider([httpx.ReadTimeout("read timed out")])

    run_id = accept(stack, open_pull(stack))
    stack.work(provider.gateway(), 1)

    _assert_failed_into_dlq(stack, run_id, "LLM_TIMEOUT")
    assert provider.calls == 1


def test_connect_timeout_is_a_timeout_too(stack: Stack) -> None:
    """Таймаут соединения - тот же `LLM_TIMEOUT`, а не общий сбой."""
    provider = FakeLlmProvider([httpx.ConnectTimeout("connect timed out")])

    run_id = accept(stack, open_pull(stack))
    stack.work(provider.gateway(), 1)

    _assert_failed_into_dlq(stack, run_id, "LLM_TIMEOUT")


def test_connection_reset_is_llm_unavailable(stack: Stack) -> None:
    """Обрыв соединения с провайдером - `LLM_UNAVAILABLE`."""
    provider = FakeLlmProvider([httpx.RemoteProtocolError("peer closed connection")])

    run_id = accept(stack, open_pull(stack))
    stack.work(provider.gateway(), 1)

    _assert_failed_into_dlq(stack, run_id, "LLM_UNAVAILABLE")


def test_429_on_every_key_fails_the_run_with_llm_rate_limited(stack: Stack) -> None:
    """Все ключи упёрлись в лимит: каждый попробован ровно раз, код - `LLM_RATE_LIMITED`."""
    provider = FakeLlmProvider([429, 429, 429])

    run_id = accept(stack, open_pull(stack))
    stack.work(provider.gateway(("key-1", "key-2", "key-3")), 1)

    _assert_failed_into_dlq(stack, run_id, "LLM_RATE_LIMITED")
    assert sorted(provider.keys_used) == ["key-1", "key-2", "key-3"]


def test_429_on_one_key_is_absorbed_by_the_next_key(stack: Stack) -> None:
    """Лимит на одном ключе: тот же вызов уходит со следующим ключом, ревью публикуется."""
    provider = FakeLlmProvider([429, REVIEW, VERIFIED])

    run_id = accept(stack, open_pull(stack))
    stack.work(provider.gateway(("key-1", "key-2")), 1)

    assert stack.run(run_id).status is ReviewRunStatus.COMPLETED
    first_call = provider.keys_used[:2]
    assert len(set(first_call)) == 2, "второй ключ не взят"
    (review,) = stack.github.reviews_of(PR)
    assert len(review.comments) == 1
    assert stack.queue_depth(DLQ_NAME) == 0


def test_mixed_429_and_5xx_is_llm_unavailable(stack: Stack) -> None:
    """Ошибка сервера сразу роняет вызов провайдера: лимит здесь ни при чём."""
    provider = FakeLlmProvider([429, 503])

    run_id = accept(stack, open_pull(stack))
    stack.work(provider.gateway(("key-1", "key-2")), 1)

    _assert_failed_into_dlq(stack, run_id, "LLM_UNAVAILABLE")


def test_primary_timeout_falls_back_to_the_second_provider(stack: Stack) -> None:
    """Первый провайдер завис - тот же запрос отвечает резервный; попытка видна шагом."""
    primary = FakeLlmProvider([httpx.ReadTimeout("read timed out"), httpx.ReadTimeout("again")])
    backup = FakeLlmProvider([REVIEW, VERIFIED])
    gateway = FallbackLlmGateway(
        [("ollama", primary.gateway(name="ollama")), ("eurorouter", backup.gateway())]
    )

    run_id = accept(stack, open_pull(stack))
    stack.work(gateway, 1)

    assert stack.run(run_id).status is ReviewRunStatus.COMPLETED
    trail = step_trail(stack, run_id)
    assert (ANALYSING, FAILED, "LLM_TIMEOUT") in trail
    assert (ANALYSING, DONE, None) in trail
    (review,) = stack.github.reviews_of(PR)
    assert len(review.comments) == 1
    assert stack.queue_depth(DLQ_NAME) == 0


def test_verifier_timeout_publishes_screened_findings_fail_open(stack: Stack) -> None:
    """Таймаут второго прохода прогон не роняет: замечание публикуется, итог - `unavailable`."""
    provider = FakeLlmProvider([REVIEW, httpx.ReadTimeout("read timed out")])

    run_id = accept(stack, open_pull(stack))
    stack.work(provider.gateway(), 1)

    run = stack.run(run_id)
    assert run.status is ReviewRunStatus.COMPLETED
    assert run.verification is VerificationOutcome.UNAVAILABLE
    assert (VERIFYING, FAILED, "LLM_TIMEOUT") in step_trail(stack, run_id)
    (review,) = stack.github.reviews_of(PR)
    assert len(review.comments) == 1
    assert stack.queue_depth(DLQ_NAME) == 0
