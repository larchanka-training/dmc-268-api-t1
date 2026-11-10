"""Симуляция песочницы целиком: `POST` → база → очередь → воркер → модель → опрос `GET` (#288).

Настоящие API, PostgreSQL, RabbitMQ и consume-цикл воркера; модель - сценарий. Сессия
подменена, пользователь - настоящая строка `users`. GitHub симуляция не трогает вовсе.
"""

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.factory import create_app
from app.domain.secret_redaction import REDACTED
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_broker, requires_db
from ..fakes.auth import SIGNED_IN, sign_in
from ..fakes.llm_gateway import (
    ScriptedLlmGateway,
    finding_json,
    review_response,
    verification_response,
)
from .stack import Stack

pytestmark = [pytest.mark.integration, requires_db, requires_broker]

ORIGIN = "https://review.example"
PATH = "app/deploy.py"
KEY_BODY = "MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn"
CODE = (
    "def deploy():\n"
    '    key = """\n'
    "-----BEGIN RSA PRIVATE KEY-----\n"
    f"{KEY_BODY}\n"
    "-----END RSA PRIVATE KEY-----\n"
    '"""\n'
    "    return key\n"
)
RULES = "## Secrets\n\n- pattern: `app/**/*.py`\n\n1. Ключи не хранятся в коде\n"
POLLS = 5


def _client(stack: Stack) -> TestClient:
    app = create_app(stack.settings.model_copy(update={"public_origin": ORIGIN}))
    app.state.container = stack.container()
    sign_in(app, SIGNED_IN)
    with SqlAlchemyUnitOfWork(stack.engine) as work:
        work.users.add(SIGNED_IN)
        work.commit()
    return TestClient(app, base_url=ORIGIN, headers={"Origin": ORIGIN})


def test_a_simulation_goes_through_the_worker_and_keeps_neither_input_nor_key(stack: Stack) -> None:
    client = _client(stack)
    model = ScriptedLlmGateway(
        [
            review_response(
                finding_json(
                    PATH,
                    3,
                    "Приватный ключ зашит в код.",
                    rule=("Secrets", 1),
                    category="security",
                    severity="critical",
                ),
                tokens=120,
            ),
            verification_response(("F1", "keep"), tokens=30),
        ]
    )

    started = client.post(
        "/api/v1/simulations",
        json={"source": {"kind": "code", "file_path": PATH, "content": CODE}, "rules": RULES},
    )
    assert started.status_code == 202, started.text
    simulation_id = started.json()["id"]
    assert started.json()["status"] == "queued"

    stack.work(model, 1)

    # Опрос, как у фронта: итог - completed или failed.
    for _ in range(POLLS):
        body = client.get(f"/api/v1/simulations/{simulation_id}").json()
        if body["status"] in {"completed", "failed"}:
            break
    assert body["status"] == "completed", body
    assert (body["tokens_used"], body["verification"]) == (150, "verified")
    (finding,) = body["findings"]
    assert (finding["new_line"], finding["severity"]) == (3, "critical")
    assert finding["rule"] == {"name": "Secrets", "check": 1, "check_text": "Ключи не хранятся в коде"}
    assert finding["verdict"]["verdict"] == "keep"

    # Модель получила ключ как есть и могла указать на него.
    assert KEY_BODY in model.requests[0].messages[-1].content
    # В сохранённом контексте на его месте метка, а входа в строке нет вовсе.
    (context,) = body["context"]
    assert REDACTED in context["hunks"][0] and KEY_BODY not in context["hunks"][0]
    with stack.engine.connect() as connection:
        row = connection.execute(
            text("SELECT source, rules, simulations::text FROM simulations WHERE id = :id"),
            {"id": UUID(simulation_id)},
        ).one()
    assert (row.source, row.rules) == (None, None)
    assert KEY_BODY not in row.simulations
    # Симуляция не ходила в GitHub.
    assert stack.github.requests == []
