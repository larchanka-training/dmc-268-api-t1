"""Вебхук против настоящей базы: частичный уникальный индекс реально держит дедуп.

Отличие от юнит-тестов use case: `SqlAlchemyUnitOfWork` настоящий, дедуп
повторной доставки держит не фейк, а частичный уникальный индекс. VCS и
очередь — двойники из `tests.fakes`: сеть и брокер не нужны. Две одинаковые
доставки дают одну запись `review_runs` и одну задачу; вторая получает
202 с проекцией существующего прогона.
"""

import hashlib
import hmac
import json
import os
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from httpx import Client, Response
from sqlalchemy import Engine, text

from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import ReviewJob
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db
from ..fakes import (
    INSTALLATION_ID,
    FakeContainer,
    FakeQueue,
    FakeVcs,
    a_repository,
    webhook_payload,
)

pytestmark = [pytest.mark.integration, requires_db]

SECRET = "test-secret"


def _signature(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Flow:
    client: TestClient
    engine: Engine
    vcs: FakeVcs
    queue: FakeQueue


@pytest.fixture
def flow(clean_db) -> Flow:
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.repositories.add(a_repository())
        work.commit()

    settings = Settings(
        database_url=os.environ["TEST_DATABASE_URL"],
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret=SECRET,
    )
    vcs = FakeVcs()
    queue = FakeQueue()
    app = create_app(settings)
    app.state.container = FakeContainer(
        engine=clean_db, settings=settings, vcs=vcs, queue=queue
    )
    return Flow(client=TestClient(app), engine=clean_db, vcs=vcs, queue=queue)


def post_webhook(client: Client) -> Response:
    body = json.dumps(webhook_payload("opened")).encode("utf-8")
    headers = {
        "X-Hub-Signature-256": _signature(body),
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }
    return client.post("/api/v1/webhooks/github", content=body, headers=headers)


def test_repeated_delivery_against_real_db_creates_one_run(flow: Flow) -> None:
    first = post_webhook(flow.client)
    second = post_webhook(flow.client)

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(flow.queue.jobs) == 1

    with flow.engine.connect() as conn:
        count: int = conn.execute(
            text("SELECT COUNT(*) FROM review_runs")
        ).scalar_one()
    assert count == 1

    # Повторная доставка — 202 с проекцией найденного прогона, а не ошибка:
    # не-2xx хостинг считает неудачной доставкой и повторяет её.
    (enqueued,) = flow.queue.jobs
    assert second.json()["id"] == str(enqueued.review_run_id)
    assert first.json() == second.json()


def test_repeated_delivery_carries_installation_id(flow: Flow) -> None:
    """Задача несёт installation_id из payload'а: воркер авторизуется той же
    инсталляцией, что и приём (спека webhook-intake)."""
    post_webhook(flow.client)

    (enqueued,) = flow.queue.jobs
    assert isinstance(enqueued, ReviewJob)
    assert enqueued.installation_id == INSTALLATION_ID
