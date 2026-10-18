"""`OllamaGateway` на записанных ответах `/api/chat`: протокол, токены, сбои."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.application.llm_schema import ReviewOutput
from app.application.ports.llm_gateway import LlmRequest, LlmTask
from app.domain.entities import PromptMessage
from app.domain.enums import PromptRole
from app.domain.llm_errors import LlmError, LlmOutputError
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

    response = _gateway(handler).complete(REQUEST)

    assert isinstance(response.content, ReviewOutput)
    assert (response.model, response.tokens_used) == ("qwen2.5-coder:7b", 1_500)


def test_fully_cached_prompt_without_prompt_eval_count_is_accepted() -> None:
    reply = _reply()
    del reply["prompt_eval_count"]
    handler, _ = _recording(httpx.Response(200, json=reply))

    assert _gateway(handler).complete(REQUEST).tokens_used == 300


def test_prompt_counted_over_the_window_minus_reserve_is_treated_as_truncated() -> None:
    handler, _ = _recording(httpx.Response(200, json=_reply(prompt_eval_count=WINDOW - 4_096 + 1)))

    with pytest.raises(LlmError, match="61441 .* 61440") as error:
        _gateway(handler).complete(REQUEST)

    assert error.value.tokens_used == WINDOW - 4_096 + 1 + 300


def test_answer_cut_by_num_predict_is_not_retried() -> None:
    handler, seen = _recording(
        httpx.Response(200, json=_reply('{"schemaVersion": "1", "fin', done_reason="length")),
        httpx.Response(200, json=_reply()),
    )

    with pytest.raises(LlmOutputError, match="обрезан пределом длины") as error:
        _gateway(handler).complete(REQUEST)

    assert len(seen) == 1
    assert error.value.tokens_used == 1_500


def test_answer_off_the_schema_is_retried_through_the_shared_loop() -> None:
    handler, seen = _recording(
        httpx.Response(200, json=_reply("не json")), httpx.Response(200, json=_reply())
    )

    response = _gateway(handler).complete(REQUEST)

    assert len(seen) == 2
    assert len(json.loads(seen[1].content)["messages"]) == 3
    assert response.tokens_used == 3_000


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
