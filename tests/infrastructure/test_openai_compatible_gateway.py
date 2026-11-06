"""`OpenAiCompatibleGateway` (Eurorouter) на записанных ответах: протокол, ключи, сбои."""

import json
import logging
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.application.llm_schema import ReviewOutput
from app.application.ports.llm_gateway import LlmMeter, LlmRequest, LlmTask
from app.domain.entities import PromptMessage
from app.domain.enums import PromptRole
from app.domain.llm_errors import LlmError, LlmRateLimitedError, LlmTimeoutError
from app.domain.llm_usage import LlmUsage
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


# `usage` ответа Eurorouter, снятый со стенда 2026-11-06 (glm-5.3), с разбивкой, - текстом
# JSON, как он пришёл: стоимость в нём не должна пройти через float.
EUROROUTER_USAGE = """{
    "prompt_tokens": 30,
    "completion_tokens": 5,
    "total_tokens": 35,
    "prompt_tokens_details": {"cached_tokens": 10},
    "completion_tokens_details": {"reasoning_tokens": 2},
    "cost": 0.0000913,
    "cost_currency": "EUR",
    "cost_eur": 0.0000913,
    "cost_details": {"upstream_inference_prompt_cost": 0.00006, "upstream_inference_completions_cost": 0.0000313}
}"""


def _raw(usage: str) -> httpx.Response:
    """Ответ с `usage`, вставленным в тело JSON дословно."""
    body = json.dumps({k: v for k, v in _reply().items() if k != "usage"})
    return httpx.Response(
        200, content=f'{body[:-1]}, "usage": {usage}}}', headers={"content-type": "application/json"}
    )


def _metered(handler: Handler) -> LlmUsage:
    """Расход, который адаптер записал в счётчик запроса."""
    meter = LlmMeter()
    _gateway(handler).complete(replace(REQUEST, meter=meter))
    return meter.total


def _gateway(
    handler: Handler, keys: tuple[str, ...] = KEYS, reasoning_effort: str | None = None
) -> OpenAiCompatibleGateway:
    client = httpx.Client(
        base_url="https://eurorouter.example/v1", transport=httpx.MockTransport(handler)
    )
    return OpenAiCompatibleGateway(
        client,
        "mistral-large-2411",
        tuple(SecretStr(key) for key in keys),
        name="Eurorouter",
        max_attempts=2,
        reasoning_effort=reasoning_effort,
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


def test_reasoning_effort_goes_into_the_body_only_when_set() -> None:
    handler, seen = _recording(httpx.Response(200, json=_reply()), httpx.Response(200, json=_reply()))

    _gateway(handler, reasoning_effort="low").complete(REQUEST)
    _gateway(handler).complete(REQUEST)

    with_effort, without = (json.loads(request.content) for request in seen)
    assert with_effort["reasoning_effort"] == "low"
    assert "reasoning_effort" not in without


def test_answer_carries_the_responding_model_and_records_tokens() -> None:
    handler, _ = _recording(httpx.Response(200, json=_reply()))
    meter = LlmMeter()

    response = _gateway(handler).complete(replace(REQUEST, meter=meter))

    assert isinstance(response.content, ReviewOutput)
    assert response.model == "mistral-large-2411"
    assert meter.total == LlmUsage(input_tokens=1_200, output_tokens=300, total_tokens=1_500)


def test_usage_is_parsed_by_kind_with_the_exact_cost() -> None:
    handler, _ = _recording(_raw(EUROROUTER_USAGE))

    usage = _metered(handler)

    assert usage == LlmUsage(
        input_tokens=30,
        output_tokens=5,
        cached_tokens=10,
        reasoning_tokens=2,
        total_tokens=35,
        cost_eur=Decimal("0.0000913"),
    )
    assert str(usage.cost_eur.normalize()) == "0.0000913"


def test_total_is_the_provider_total_even_when_kinds_disagree() -> None:
    # Как на develop: `tokens_used` - `usage.total_tokens` провайдера, а не сумма видов.
    handler, _ = _recording(_raw('{"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 40}'))

    assert _metered(handler).total_tokens == 40


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ('{"prompt_tokens": 30, "completion_tokens": 5}', LlmUsage(input_tokens=30, output_tokens=5, total_tokens=35)),
        ('{"total_tokens": 35}', LlmUsage(total_tokens=35)),
        (
            (
                '{"prompt_tokens": 30, "completion_tokens": null, "total_tokens": null,'
                ' "prompt_tokens_details": null, "completion_tokens_details": {"reasoning_tokens": null}}'
            ),
            LlmUsage(input_tokens=30, total_tokens=30),
        ),
        ("{}", LlmUsage()),
    ],
    ids=["no-total", "total-only", "nulls", "empty"],
)
def test_missing_or_null_counts_are_zero_and_the_total_falls_back_to_the_kinds(
    usage: str, expected: LlmUsage
) -> None:
    handler, _ = _recording(_raw(usage))

    assert _metered(handler) == expected


@pytest.mark.parametrize(
    "usage",
    ['{"total_tokens": "35"}', '{"total_tokens": true}', '{"prompt_tokens": -1}', '{"total_tokens": 3.5}'],
    ids=["string", "bool", "negative", "fraction"],
)
def test_malformed_counts_are_an_unexpected_body(usage: str) -> None:
    handler, _ = _recording(_raw(usage))

    with pytest.raises(LlmError, match="неожиданное тело"):
        _gateway(handler).complete(REQUEST)


@pytest.mark.parametrize(
    ("cost", "cost_eur"),
    [
        ('"cost": 0.0012345, "cost_currency": "EUR"', Decimal("0.0012345")),
        ('"cost_eur": 0', Decimal(0)),
        ('"cost": 0.5, "cost_currency": "USD"', Decimal(0)),
        ('"cost_eur": null, "cost": 0.5, "cost_currency": "USD"', Decimal(0)),
        ('"cost_eur": "не число"', Decimal(0)),
        ('"cost_eur": -1.0', Decimal(0)),
        ('"cost_eur": true', Decimal(0)),
        ('"cost_eur": 1e30', Decimal(0)),
        ('"cost_eur": NaN', Decimal(0)),
    ],
    ids=["cost-in-eur", "zero", "cost-in-usd", "null", "not-a-number", "negative", "bool", "huge", "nan"],
)
def test_cost_falls_back_to_eur_cost_or_zero(cost: str, cost_eur: Decimal) -> None:
    handler, _ = _recording(_raw(f'{{"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35, {cost}}}'))

    assert _metered(handler) == LlmUsage(input_tokens=30, output_tokens=5, total_tokens=35, cost_eur=cost_eur)


@pytest.mark.parametrize(
    ("cost", "stored"),
    [
        ("0.00000000004", "0E-10"),
        ("0.00000000005", "0E-10"),
        ("0.00000000015", "2E-10"),
        ("0.00000000025", "2E-10"),
        ("0.000000000251", "3E-10"),
        ("13.0960038381", "13.0960038381"),
    ],
)
def test_cost_is_kept_to_ten_places_rounding_half_to_even(cost: str, stored: str) -> None:
    handler, _ = _recording(_raw(f'{{"total_tokens": 1, "cost_eur": {cost}}}'))

    assert _metered(handler).cost_eur == Decimal(stored)


@pytest.mark.parametrize(
    "usage",
    ["null", '"сломано"', '{"prompt_tokens": 30, "completion_tokens": 5, "prompt_tokens_details": 7, "completion_tokens_details": null}'],
    ids=["usage-null", "usage-not-object", "details-not-object"],
)
def test_answer_without_usable_usage_is_accepted_not_paid_twice(usage: str) -> None:
    """Оплаченный ответ с битым `usage` принят: отказ отправил бы повтор и заплатил второй раз."""
    handler, seen = _recording(_raw(usage))

    _metered(handler)

    assert len(seen) == 1


def test_answer_without_usage_key_is_accepted_with_zero_usage(caplog: pytest.LogCaptureFixture) -> None:
    body = {k: v for k, v in _reply().items() if k != "usage"}
    handler, seen = _recording(httpx.Response(200, json=body))

    with caplog.at_level(logging.WARNING):
        usage = _metered(handler)

    assert len(seen) == 1
    assert usage.total_tokens == 0
    assert any("нет счётчиков токенов" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    ("usage", "warning"),
    [
        ("{}", "нет счётчиков токенов"),
        ('{"prompt_tokens": 30, "completion_tokens": 5}', "нет стоимости в евро"),
    ],
    ids=["no-counters", "no-cost"],
)
def test_usage_counted_as_zero_is_logged(usage: str, warning: str, caplog: pytest.LogCaptureFixture) -> None:
    """Ответ оплачен и принят, но ноль в учёте не молчаливый: его не увидят квота и бюджет."""
    handler, _ = _recording(_raw(usage))

    with caplog.at_level(logging.WARNING):
        _metered(handler)

    assert any(warning in record.getMessage() for record in caplog.records)


def test_rejected_key_adds_no_usage_and_the_answering_key_adds_its_own() -> None:
    handler, _ = _recording(httpx.Response(429), _raw(EUROROUTER_USAGE))

    assert _metered(handler).cost_eur == Decimal("0.0000913")


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


def test_every_key_rate_limited_is_a_rate_limit_failure() -> None:
    handler, seen = _recording(httpx.Response(429), httpx.Response(429))

    with pytest.raises(LlmRateLimitedError, match="лимит запросов на всех ключах") as caught:
        _gateway(handler).complete(REQUEST)

    assert caught.value.code == "LLM_RATE_LIMITED"
    assert len(seen) == 2


def test_keys_rejected_for_mixed_reasons_are_a_plain_provider_failure() -> None:
    handler, _ = _recording(httpx.Response(429), httpx.Response(401))

    with pytest.raises(LlmError, match="все ключи отвергнуты") as caught:
        _gateway(handler).complete(REQUEST)

    assert type(caught.value) is LlmError
    assert caught.value.code == "LLM_UNAVAILABLE"


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
    meter = LlmMeter()

    _gateway(handler).complete(replace(REQUEST, meter=meter))

    assert len(json.loads(seen[1].content)["messages"]) == 3
    assert meter.total.total_tokens == 3_000


def test_answer_cut_by_max_tokens_is_retried_naming_the_limit() -> None:
    cut = _reply('{"schemaVersion": "1", "fin')
    cut["choices"][0]["finish_reason"] = "length"
    handler, seen = _recording(httpx.Response(200, json=cut), httpx.Response(200, json=_reply()))
    meter = LlmMeter()

    _gateway(handler).complete(replace(REQUEST, meter=meter))

    assert len(seen) == 2
    retry = json.loads(seen[1].content)["messages"][-1]["content"]
    assert "обрезан пределом длины (4096 токенов)" in retry
    assert "не JSON" not in retry
    assert meter.total.total_tokens == 3_000


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

    with pytest.raises(LlmTimeoutError, match="таймаут"):
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
