"""Обёртка `MeteredLlmGateway`: токены успешных вызовов модели — в метриках. Без сети."""

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app.application.llm_schema import ReviewOutput
from app.application.ports.llm_gateway import LlmRequest, LlmTask
from app.domain.entities import PromptMessage
from app.domain.enums import PromptRole
from app.domain.llm_errors import LlmTimeoutError
from app.infrastructure.llm.metered import MeteredLlmGateway

from ..fakes.llm_gateway import ScriptedLlmGateway, review_response
from ..metrics import metric_delta, metric_snapshot

_MESSAGES = (PromptMessage(PromptRole.SYSTEM, "инструкции"),)


def _request() -> LlmRequest[ReviewOutput]:
    return LlmRequest(LlmTask.REVIEW, _MESSAGES, ReviewOutput, max_output_tokens=4_096)


def test_tokens_of_a_successful_call_are_recorded_by_task_and_model(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Расход вызова `complete` — в счётчике по задаче прогона и ответившей модели.

    Токены повторов и фоллбэка уже просуммированы в ответе шлюза, поэтому
    обёртка видит весь расход один раз.
    """
    before = metric_snapshot(metric_reader)
    gateway = MeteredLlmGateway(
        ScriptedLlmGateway([review_response(tokens=30, model="stub-llm")])
    )

    gateway.complete(_request())

    tokens, _ = metric_delta(
        metric_reader, before, "review_llm_tokens", task="review", model="stub-llm"
    )
    assert tokens == 30


def test_a_failed_call_is_not_recorded(metric_reader: InMemoryMetricReader) -> None:
    """Сбойного вызова в счётчике нет: его токены доходят до прогона через исключение."""
    before = metric_snapshot(metric_reader)
    gateway = MeteredLlmGateway(
        ScriptedLlmGateway([LlmTimeoutError("таймаут", tokens_used=30)])
    )

    with pytest.raises(LlmTimeoutError):
        gateway.complete(_request())

    tokens, _ = metric_delta(
        metric_reader, before, "review_llm_tokens", task="review", model="stub-llm"
    )
    assert tokens == 0
