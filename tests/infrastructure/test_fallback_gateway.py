"""`FallbackLlmGateway`: провайдеры по порядку, первый ответ, сбой последнего."""

import logging
from dataclasses import replace

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


def test_tokens_burnt_by_a_failed_provider_are_added_to_the_answer() -> None:
    local = ScriptedLlmGateway([LlmOutputError("не по схеме", tokens_used=500)])
    hosted = ScriptedLlmGateway([review_response(model="mistral", tokens=300)])

    response = FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted))).complete(REQUEST)

    assert response.tokens_used == 800


def test_tokens_of_every_provider_are_reported_when_all_fail() -> None:
    local = ScriptedLlmGateway([LlmOutputError("не по схеме", tokens_used=500)])
    hosted = ScriptedLlmGateway([LlmError("Eurorouter недоступен")])

    with pytest.raises(LlmError, match="Eurorouter") as error:
        FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted))).complete(REQUEST)

    assert error.value.tokens_used == 500


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


class _RetriesOnce:
    """Провайдер, который сам повторил вызов один раз (100 токенов), потом ответил по сценарию."""

    def __init__(self, answer: object) -> None:
        self._scripted = ScriptedLlmGateway([answer])  # type: ignore[list-item]

    def complete(self, request):
        assert request.on_retry is not None
        request.on_retry(LlmOutputError("не по схеме"), 100)
        return self._scripted.complete(request)


def test_handover_and_retries_inside_providers_reach_the_listener_with_running_tokens() -> None:
    heard: list[tuple[str, int]] = []
    request = replace(REQUEST, on_retry=lambda error, tokens: heard.append((str(error), tokens)))
    local = _RetriesOnce(LlmOutputError("Ollama: не по схеме", tokens_used=200))
    hosted = _RetriesOnce(review_response(model="mistral", tokens=300))

    response = FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted))).complete(request)

    # Повтор внутри Ollama, переход к Eurorouter, повтор внутри Eurorouter: токены -
    # от начала всего вызова, итог ответа - тоже.
    assert heard == [("не по схеме", 100), ("Ollama: не по схеме", 200), ("не по схеме", 300)]
    assert response.tokens_used == 500


def test_a_request_without_a_listener_reaches_providers_unchanged() -> None:
    local = ScriptedLlmGateway([LlmError("Ollama недоступна", tokens_used=10)])
    hosted = ScriptedLlmGateway([review_response()])

    FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted))).complete(REQUEST)

    assert hosted.requests[0] is REQUEST
