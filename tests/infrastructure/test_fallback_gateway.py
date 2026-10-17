"""`FallbackLlmGateway`: провайдеры по порядку, первый ответ, сбой последнего."""

import logging

import pytest

from app.application.llm_schema import ReviewOutput
from app.application.ports.llm_gateway import LlmRequest, LlmTask
from app.domain.entities import PromptMessage
from app.domain.enums import PromptRole
from app.domain.llm_errors import LlmError, LlmOutputError
from app.infrastructure.llm.fallback import FallbackLlmGateway

from ..fakes.llm_gateway import ScriptedLlmGateway, review_response

MESSAGES = (PromptMessage(PromptRole.USER, "код"),)
REQUEST = LlmRequest(LlmTask.REVIEW, MESSAGES, ReviewOutput, max_output_tokens=4_096)


def test_first_provider_answer_is_returned_and_the_second_is_not_called() -> None:
    local = ScriptedLlmGateway([review_response(model="qwen")])
    hosted = ScriptedLlmGateway([])

    response = FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted))).complete(REQUEST)

    assert response.model == "qwen"
    assert hosted.requests == []


@pytest.mark.parametrize(
    "failure",
    [LlmError("Ollama: таймаут"), LlmOutputError("ответ модели не прошёл схему")],
    ids=["transport", "schema"],
)
def test_failed_provider_hands_the_same_request_to_the_next(failure: LlmError) -> None:
    local = ScriptedLlmGateway([failure])
    hosted = ScriptedLlmGateway([review_response(model="mistral")])

    response = FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted))).complete(REQUEST)

    assert response.model == "mistral"
    assert hosted.requests == [REQUEST]


def test_when_every_provider_fails_the_last_failure_is_raised() -> None:
    local = ScriptedLlmGateway([LlmError("Ollama недоступна")])
    hosted = ScriptedLlmGateway([LlmOutputError("Eurorouter: ответ не по схеме")])

    with pytest.raises(LlmOutputError, match="Eurorouter"):
        FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted))).complete(REQUEST)


def test_single_provider_failure_is_raised_as_is() -> None:
    local = ScriptedLlmGateway([LlmError("Ollama недоступна")])

    with pytest.raises(LlmError, match="Ollama недоступна"):
        FallbackLlmGateway((("Ollama", local),)).complete(REQUEST)


def test_handover_is_logged_with_provider_and_failure_class(
    caplog: pytest.LogCaptureFixture,
) -> None:
    local = ScriptedLlmGateway([LlmOutputError("не по схеме")])
    hosted = ScriptedLlmGateway([review_response()])

    with caplog.at_level(logging.WARNING):
        FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted))).complete(REQUEST)

    (record,) = caplog.records
    assert record.getMessage() == (
        "модель Ollama не ответила на review (LlmOutputError: не по схеме), запрос уходит Eurorouter"
    )


def test_other_exceptions_are_not_swallowed() -> None:
    local = ScriptedLlmGateway([RuntimeError("ошибка программы")])
    hosted = ScriptedLlmGateway([])

    with pytest.raises(RuntimeError):
        FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted))).complete(REQUEST)

    assert hosted.requests == []


def test_gateway_without_providers_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="хотя бы один провайдер"):
        FallbackLlmGateway(())
