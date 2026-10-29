"""`FallbackLlmGateway`: провайдеры по порядку, первый ответ, сбой последнего."""

import logging
from dataclasses import replace

import pytest
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from app.application.llm_schema import ReviewOutput
from app.application.ports.llm_gateway import LlmRequest, LlmTask
from app.domain.entities import PromptMessage
from app.domain.enums import PromptRole
from app.domain.llm_errors import LlmError, LlmOutputError, LlmTimeoutError
from app.infrastructure.llm.fallback import FallbackLlmGateway

from ..fakes.llm_gateway import ScriptedLlmGateway, review_response
from ..metrics import metric_delta, metric_snapshot

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
    """Текст ошибки в лог не пишется: в нём бывает ответ провайдера (секрет,
    до 200 символов текста валидации). Диагноз — тип исключения и его код
    из каталога ошибок; в трейсах тот же текст гасит редакция коллектора."""
    local = ScriptedLlmGateway([LlmOutputError("не по схеме: поле findings")])
    hosted = ScriptedLlmGateway([review_response()])

    with caplog.at_level(logging.WARNING):
        FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted))).complete(REQUEST)

    (record,) = caplog.records
    assert record.getMessage() == (
        "модель Ollama не ответила на review (LlmOutputError, код LLM_INVALID_OUTPUT), "
        "запрос уходит Eurorouter"
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


# --- длительность вызовов: на уровне провайдера, с исходом ---------------------------


def test_each_provider_call_lands_in_the_duration_histogram_with_its_outcome(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Длительность — на каждый фактический вызов провайдера, с исходом.

    Успешный вызов несёт модель ответа, сбой (включая таймаут) — имя
    провайдера: фоллбэк виден двумя записями, а не одной суммой под моделью
    ответившего последним. Метрика заявлена аудитом #89 как время КАЖДОГО
    вызова модели — упавшие вызовы из неё не выпадают.
    """
    local = ScriptedLlmGateway([LlmTimeoutError("нет ответа за таймаут")])
    hosted = ScriptedLlmGateway([review_response(model="mistral")])
    gateway = FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted)))

    before = metric_snapshot(metric_reader)
    gateway.complete(REQUEST)

    failed, _ = metric_delta(
        metric_reader, before, "review_llm_call_duration",
        task="review", provider="Ollama", outcome="error",
    )
    answered, _ = metric_delta(
        metric_reader, before, "review_llm_call_duration",
        task="review", provider="Eurorouter", model="mistral", outcome="ok",
    )
    assert failed == 1
    assert answered == 1


def test_the_histogram_records_the_measured_duration(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Запись несёт измеренное время вызова, а не счёт наблюдений: часы для
    теста идут шагами по полторы секунды, каждая запись — ровно один шаг."""
    ticks = 0.0

    def clock() -> float:
        nonlocal ticks
        ticks += 1.5
        return ticks

    gateway = FallbackLlmGateway(
        (("Ollama", ScriptedLlmGateway([review_response(model="mistral")])),),
        clock=clock,
    )

    before = metric_snapshot(metric_reader)
    gateway.complete(REQUEST)

    calls, total = metric_delta(
        metric_reader, before, "review_llm_call_duration",
        task="review", provider="Ollama", model="mistral", outcome="ok",
    )
    assert calls == 1
    assert total == pytest.approx(1.5)


def test_calls_failing_everywhere_still_reach_the_histogram(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Вызов, упавший у всех провайдеров, остаётся в гистограмме: каждая
    попытка — запись с исходом `error` и именем своего провайдера."""
    local = ScriptedLlmGateway([LlmError("нет связи")])
    hosted = ScriptedLlmGateway([LlmOutputError("ответ не по схеме")])
    gateway = FallbackLlmGateway((("Ollama", local), ("Eurorouter", hosted)))

    before = metric_snapshot(metric_reader)
    with pytest.raises(LlmOutputError):
        gateway.complete(REQUEST)

    for provider in ("Ollama", "Eurorouter"):
        calls, _ = metric_delta(
            metric_reader, before, "review_llm_call_duration",
            task="review", provider=provider, outcome="error",
        )
        assert calls == 1


def test_calls_create_no_token_counter(
    metric_reader: InMemoryMetricReader,
) -> None:
    """Токены модели — не метрика этого PR: учёт по каждому вызову провайдера,
    с типами токенов и стоимостью, переделывает #119, счётчик удалён."""
    gateway = FallbackLlmGateway((("Ollama", ScriptedLlmGateway([review_response(tokens=30)])),))

    gateway.complete(REQUEST)

    assert [key for key in metric_snapshot(metric_reader) if key[0] == "review_llm_tokens"] == []
