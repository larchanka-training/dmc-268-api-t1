"""Сквозной путь стоимости прогона: тело ответа Eurorouter → воркер → PostgreSQL.

Модель - настоящий `OpenAiCompatibleGateway` над записанным телом `/chat/completions`,
в котором стоимость стоит числом JSON. Тест сверяет, что она доходит до колонки
`review_runs.cost_eur` и до суммы SQL точно, без следа float.
"""

import json
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import text

from app.domain.enums import ReviewRunStatus
from app.infrastructure.llm.openai_compatible import OpenAiCompatibleGateway

from ..conftest import requires_broker, requires_db
from .stack import Stack, deliver

pytestmark = [pytest.mark.integration, requires_db, requires_broker]

EMPTY_REVIEW = json.dumps(
    {"schemaVersion": "1", "findings": [], "summary": {"problem": "п", "positives": [], "effort": "э"}}
)
# Стоимость - числом JSON, как в ответе Eurorouter (стенд, 2026-11-06).
USAGE = '{"prompt_tokens": 30, "completion_tokens": 5, "total_tokens": 35, "cost_currency": "EUR", "cost_eur": 0.0000913}'


def _eurorouter() -> OpenAiCompatibleGateway:
    def handler(request: httpx.Request) -> httpx.Response:
        choice = {"message": {"role": "assistant", "content": EMPTY_REVIEW}, "finish_reason": "stop"}
        body = json.dumps({"model": "glm-5.3", "choices": [choice]})
        return httpx.Response(
            200, content=f'{body[:-1]}, "usage": {USAGE}}}', headers={"content-type": "application/json"}
        )

    client = httpx.Client(base_url="https://eurorouter.test/v1", transport=httpx.MockTransport(handler))
    return OpenAiCompatibleGateway(client, "glm-5.3", [SecretStr("k")], name="Eurorouter", max_attempts=1)


def test_the_cost_from_the_provider_body_reaches_the_run_row_exactly(stack: Stack) -> None:
    pull = stack.github.open_pull(
        7,
        base_sha="b0" * 20,
        head_sha="c1" * 20,
        base_files={},
        head_files={"app/x.py": "x = 1\n"},
        title="feat: x",
    )
    response = deliver(TestClient(stack.api()), pull)
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]

    stack.work(_eurorouter(), 1)

    run = stack.run(run_id)
    assert run.status is ReviewRunStatus.COMPLETED
    assert run.tokens_used == 35
    assert run.usage is not None and run.usage.cost_eur == Decimal("0.0000913")
    with stack.engine.connect() as connection:
        stored = connection.execute(text("SELECT sum(cost_eur)::text FROM review_runs")).scalar_one()
    assert stored == "0.0000913000"
