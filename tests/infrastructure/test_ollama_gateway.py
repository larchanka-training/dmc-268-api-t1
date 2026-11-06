"""`OllamaGateway` на записанных ответах `/api/chat`: протокол, токены, сбои."""

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

import httpx
import pytest

from app.application.llm_schema import ReviewOutput
from app.application.ports.llm_gateway import LlmMeter, LlmRequest, LlmTask
from app.domain.entities import PromptMessage
from app.domain.enums import PromptRole
from app.domain.llm_errors import LlmError, LlmTimeoutError
from app.domain.llm_usage import LlmUsage
from app.infrastructure.llm.ollama import OllamaGateway

Handler = Callable[[httpx.Request], httpx.Response]

MESSAGES = (PromptMessage(PromptRole.SYSTEM, "инструкции"), PromptMessage(PromptRole.USER, "код"))
REQUEST = LlmRequest(LlmTask.REVIEW, MESSAGES, ReviewOutput, max_output_tokens=4_096)
WINDOW = 65_536
VALID = json.dumps(
    {
        "schemaVersion": "1",
        "findings": [],
        "summary": {"problem": "p", "positives": [], "effort": "e"},
    }
)


def _reply(content: str = VALID, **fields: Any) -> dict[str, Any]:
    return {
        "model": "qwen2.5-coder:7b",
        "message": {"role": "assistant", "content": content},
        "done": True,
        "prompt_eval_count": 1_200,
        "eval_count": 300,
        **fields,
    }


def _gateway(handler: Handler, *, max_attempts: int = 2) -> OllamaGateway:
    client = httpx.Client(base_url="http://ollama:11434", transport=httpx.MockTransport(handler))
    return OllamaGateway(client, "qwen2.5-coder:7b", window_tokens=WINDOW, max_attempts=max_attempts)


def _metered(handler: Handler, *, max_attempts: int = 2) -> LlmMeter:
    meter = LlmMeter()
    _gateway(handler, max_attempts=max_attempts).complete(replace(REQUEST, meter=meter))
    return meter


def _recording(*replies: httpx.Response) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    return handler, seen


def test_request_body_carries_messages_schema_window_and_output_limit() -> None:
    handler, seen = _recording(httpx.Response(200, json=_reply()))

    _gateway(handler).complete(REQUEST)

    (request,) = seen
    assert request.method == "POST"
    assert request.url.path == "/api/chat"
    assert json.loads(request.content) == {
        "model": "qwen2.5-coder:7b",
        "messages": [
            {"role": "system", "content": "инструкции"},
            {"role": "user", "content": "код"},
        ],
        "format": ReviewOutput.model_json_schema(),
        "stream": False,
        "options": {"num_ctx": WINDOW, "num_predict": 4_096, "temperature": 0},
    }


def test_answer_carries_the_responding_model_and_both_token_counts() -> None:
    handler, _ = _recording(httpx.Response(200, json=_reply()))

    meter = LlmMeter()

    response = _gateway(handler).complete(replace(REQUEST, meter=meter))

    assert isinstance(response.content, ReviewOutput)
    assert response.model == "qwen2.5-coder:7b"
    # Стоимости у локальной модели нет.
    assert meter.total == LlmUsage(input_tokens=1_200, output_tokens=300, total_tokens=1_500)


def test_fully_cached_prompt_without_prompt_eval_count_is_accepted() -> None:
    reply = _reply()
    del reply["prompt_eval_count"]
    handler, _ = _recording(httpx.Response(200, json=reply))

    assert _metered(handler).total == LlmUsage(output_tokens=300, total_tokens=300)


def test_null_token_counts_are_zero_not_a_crash() -> None:
    handler, _ = _recording(httpx.Response(200, json=_reply(prompt_eval_count=None, eval_count=None)))

    assert _metered(handler).total == LlmUsage()


def test_integral_float_count_is_an_integer() -> None:
    """Ollama разбирает JSON без `parse_float=Decimal`: `12.0` приходит float и всё равно целое."""
    handler, _ = _recording(httpx.Response(200, json=_reply(prompt_eval_count=12.0, eval_count=3)))

    assert _metered(handler).total == LlmUsage(input_tokens=12, output_tokens=3, total_tokens=15)


def test_malformed_token_count_is_zero_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    """Кривой счётчик не отвергает ответ: правило одно для всех адаптеров."""
    handler, seen = _recording(httpx.Response(200, json=_reply(eval_count="300")))

    with caplog.at_level(logging.WARNING):
        usage = _metered(handler).total

    assert len(seen) == 1
    assert usage.output_tokens == 0
    assert any("не разобрано" in record.getMessage() for record in caplog.records)


def test_prompt_counted_over_the_window_minus_reserve_is_treated_as_truncated() -> None:
    handler, _ = _recording(httpx.Response(200, json=_reply(prompt_eval_count=WINDOW - 4_096 + 1)))

    meter = LlmMeter()

    with pytest.raises(LlmError, match="61441 .* 61440"):
        _gateway(handler).complete(replace(REQUEST, meter=meter))

    # Ответ отвергнут, но получен: его расход уже в счётчике.
    assert meter.total == LlmUsage(
        input_tokens=WINDOW - 4_096 + 1, output_tokens=300, total_tokens=WINDOW - 4_096 + 1 + 300
    )


def test_answer_cut_by_num_predict_is_retried_naming_the_limit() -> None:
    handler, seen = _recording(
        httpx.Response(200, json=_reply('{"schemaVersion": "1", "fin', done_reason="length")),
        httpx.Response(200, json=_reply()),
    )

    meter = _metered(handler)

    assert len(seen) == 2
    retry = json.loads(seen[1].content)["messages"][-1]["content"]
    assert "обрезан пределом длины (4096 токенов)" in retry
    assert "не JSON" not in retry
    assert meter.total.total_tokens == 3_000


def test_answer_off_the_schema_is_retried_through_the_shared_loop() -> None:
    handler, seen = _recording(
        httpx.Response(200, json=_reply("не json")), httpx.Response(200, json=_reply())
    )

    meter = _metered(handler)

    assert len(seen) == 2
    assert len(json.loads(seen[1].content)["messages"]) == 3
    assert meter.total.total_tokens == 3_000


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(500, json={"error": "out of memory"}), "HTTP 500: out of memory"),
        (httpx.Response(404, json={"error": "model 'x' not found"}), "HTTP 404: model 'x' not found"),
        (httpx.Response(200, text="не json"), "неожиданное тело"),
        (httpx.Response(200, json={"model": "m"}), "неожиданное тело"),
    ],
    ids=["server-error", "model-missing", "not-json", "no-message"],
)
def test_failed_reply_is_a_model_failure(response: httpx.Response, reason: str) -> None:
    handler, _ = _recording(response)

    with pytest.raises(LlmError, match=reason) as error:
        _gateway(handler).complete(REQUEST)

    assert "инструкции" not in str(error.value)


@pytest.mark.parametrize(
    "missing",
    ["message", "model"],
)
def test_counted_reply_without_an_answer_records_usage_and_fails(missing: str) -> None:
    body = {k: v for k, v in _reply().items() if k != missing}
    handler, seen = _recording(httpx.Response(200, json=body))
    meter = LlmMeter()

    with pytest.raises(LlmError, match="неожиданное тело") as error:
        _gateway(handler).complete(replace(REQUEST, meter=meter))

    assert len(seen) == 1
    assert isinstance(error.value.__cause__, KeyError)
    assert meter.total == LlmUsage(input_tokens=1_200, output_tokens=300, total_tokens=1_500)


def test_body_that_is_not_an_object_is_an_unexpected_body() -> None:
    handler, _ = _recording(httpx.Response(200, json=[_reply()]))

    with pytest.raises(LlmError, match="неожиданное тело"):
        _gateway(handler).complete(REQUEST)


@pytest.mark.parametrize(
    ("raised", "reason"),
    [(httpx.ReadTimeout, "таймаут"), (httpx.ConnectError, "недоступна")],
    ids=["timeout", "connection"],
)
def test_transport_failure_is_a_model_failure(raised: type[httpx.RequestError], reason: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised("boom", request=request)

    with pytest.raises(LlmError, match=reason) as error:
        _gateway(handler).complete(REQUEST)

    # Исключение httpx держит запрос с кодом проверяемого репозитория.
    assert error.value.__cause__ is None
    assert error.value.__suppress_context__


def test_timeout_is_classified_as_a_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    with pytest.raises(LlmTimeoutError, match="таймаут"):
        _gateway(handler).complete(REQUEST)
