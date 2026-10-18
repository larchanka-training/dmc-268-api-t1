"""`OpenAiCompatibleGateway` (Eurorouter) на записанных ответах: протокол, ключи, сбои."""

import json
import logging
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.application.llm_schema import ReviewOutput
from app.application.ports.llm_gateway import LlmRequest, LlmTask
from app.domain.entities import PromptMessage
from app.domain.enums import PromptRole
from app.domain.llm_errors import LlmError, LlmOutputError
from app.infrastructure.llm.openai_compatible import OpenAiCompatibleGateway

Handler = Callable[[httpx.Request], httpx.Response]

KEYS = ("sk-secret-one", "sk-secret-two")
MESSAGES = (PromptMessage(PromptRole.SYSTEM, "инструкции"), PromptMessage(PromptRole.USER, "код"))
REQUEST = LlmRequest(LlmTask.REVIEW, MESSAGES, ReviewOutput, max_output_tokens=4_096)
VALID = json.dumps(
    {
        "schemaVersion": "1",
        "findings": [],
        "summary": {"problem": "p", "positives": [], "effort": "e"},
    }
)


def _reply(content: str = VALID) -> dict[str, Any]:
    return {
        "model": "mistral-large-2411",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 1_200, "completion_tokens": 300, "total_tokens": 1_500},
    }


def _gateway(handler: Handler, keys: tuple[str, ...] = KEYS) -> OpenAiCompatibleGateway:
    client = httpx.Client(
        base_url="https://eurorouter.example/v1", transport=httpx.MockTransport(handler)
    )
    return OpenAiCompatibleGateway(
        client,
        "mistral-large-2411",
        tuple(SecretStr(key) for key in keys),
        name="Eurorouter",
        max_attempts=2,
    )


def _recording(*replies: httpx.Response) -> tuple[Handler, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    queue = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return queue.pop(0)

    return handler, seen


def _key(request: httpx.Request) -> str:
    return request.headers["Authorization"].removeprefix("Bearer ")


def test_request_body_carries_schema_output_limit_and_bearer_key() -> None:
    handler, seen = _recording(httpx.Response(200, json=_reply()))

    _gateway(handler).complete(REQUEST)

    (request,) = seen
    assert request.method == "POST"
    assert str(request.url) == "https://eurorouter.example/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer sk-secret-one"
    assert json.loads(request.content) == {
        "model": "mistral-large-2411",
        "messages": [
            {"role": "system", "content": "инструкции"},
            {"role": "user", "content": "код"},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "ReviewOutput",
                "schema": ReviewOutput.model_json_schema(),
                "strict": False,
            },
        },
        "max_tokens": 4_096,
        "temperature": 0,
    }


def test_answer_carries_the_responding_model_and_total_tokens() -> None:
    handler, _ = _recording(httpx.Response(200, json=_reply()))

    response = _gateway(handler).complete(REQUEST)

    assert isinstance(response.content, ReviewOutput)
    assert (response.model, response.tokens_used) == ("mistral-large-2411", 1_500)


def test_rate_limited_key_is_replaced_by_the_next_one_in_the_same_call() -> None:
    handler, seen = _recording(httpx.Response(429), httpx.Response(200, json=_reply()))

    response = _gateway(handler).complete(REQUEST)

    assert [_key(r) for r in seen] == ["sk-secret-one", "sk-secret-two"]
    assert response.model == "mistral-large-2411"


def test_every_key_rejected_is_a_provider_failure() -> None:
    handler, seen = _recording(httpx.Response(401), httpx.Response(403))

    with pytest.raises(LlmError, match="все ключи отвергнуты"):
        _gateway(handler).complete(REQUEST)

    assert len(seen) == 2


def test_consecutive_calls_start_from_different_keys() -> None:
    handler, seen = _recording(
        httpx.Response(200, json=_reply()), httpx.Response(200, json=_reply())
    )
    gateway = _gateway(handler)

    gateway.complete(REQUEST)
    gateway.complete(REQUEST)

    assert [_key(r) for r in seen] == ["sk-secret-one", "sk-secret-two"]


def test_server_error_fails_at_once_without_trying_other_keys() -> None:
    handler, seen = _recording(httpx.Response(503))

    with pytest.raises(LlmError, match="HTTP 503"):
        _gateway(handler).complete(REQUEST)

    assert len(seen) == 1


def test_answer_off_the_schema_is_retried_through_the_shared_loop() -> None:
    handler, seen = _recording(
        httpx.Response(200, json=_reply("не json")), httpx.Response(200, json=_reply())
    )

    response = _gateway(handler).complete(REQUEST)

    assert len(json.loads(seen[1].content)["messages"]) == 3
    assert response.tokens_used == 3_000


def test_answer_cut_by_max_tokens_is_not_retried() -> None:
    cut = _reply('{"schemaVersion": "1", "fin')
    cut["choices"][0]["finish_reason"] = "length"
    handler, seen = _recording(httpx.Response(200, json=cut), httpx.Response(200, json=_reply()))

    with pytest.raises(LlmOutputError, match="обрезан пределом длины") as error:
        _gateway(handler).complete(REQUEST)

    assert len(seen) == 1
    assert error.value.tokens_used == 1_500


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (httpx.Response(200, text="не json"), "неожиданное тело"),
        (httpx.Response(200, json={"model": "m", "choices": []}), "неожиданное тело"),
    ],
    ids=["not-json", "no-choices"],
)
def test_unexpected_body_is_a_model_failure(response: httpx.Response, reason: str) -> None:
    handler, _ = _recording(response)

    with pytest.raises(LlmError, match=reason):
        _gateway(handler).complete(REQUEST)


def test_timeout_is_a_model_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("boom", request=request)

    with pytest.raises(LlmError, match="таймаут"):
        _gateway(handler).complete(REQUEST)


def test_keys_never_reach_errors_or_logs(caplog: pytest.LogCaptureFixture) -> None:
    echoed = {"error": {"message": "Incorrect API key provided: sk-secret-one"}}
    handler, _ = _recording(httpx.Response(429), httpx.Response(400, json=echoed))

    with caplog.at_level(logging.DEBUG), pytest.raises(LlmError) as error:
        _gateway(handler).complete(REQUEST)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    for key in KEYS:
        assert key not in str(error.value)
        assert key not in logged
    assert "HTTP 400" in str(error.value)


def test_gateway_without_keys_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="Eurorouter: нужен хотя бы один ключ"):
        _gateway(lambda request: httpx.Response(200), keys=())
