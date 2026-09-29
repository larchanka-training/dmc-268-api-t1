"""Вебхук против настоящей базы: частичный уникальный индекс реально держит дедуп."""

import hashlib
import hmac
import json
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.api.dependencies import get_job_queue
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import ReviewJob

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

SECRET = "test-secret"

PAYLOAD = {
    "action": "opened",
    "pull_request": {
        "number": 42,
        "title": "Fix the thing",
        "body": "Описание",
        "user": {"login": "octocat"},
        "head": {"ref": "feature", "sha": "abc123def"},
        "base": {"ref": "main", "sha": "fed654cba"},
    },
    "repository": {
        "id": 987654,
        "full_name": "owner/repo",
        "default_branch": "main",
    },
}


class FakeJobQueue:
    def __init__(self) -> None:
        self.enqueued: list[ReviewJob] = []

    def enqueue(self, job: ReviewJob) -> None:
        self.enqueued.append(job)


def _signature(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_repeated_delivery_against_real_db_creates_one_run(clean_db) -> None:
    settings = Settings(
        database_url=os.environ["TEST_DATABASE_URL"],
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret=SECRET,
    )
    app = create_app(settings)
    queue = FakeJobQueue()
    app.dependency_overrides[get_job_queue] = lambda: queue
    client = TestClient(app)

    body = json.dumps(PAYLOAD).encode()
    headers = {
        "X-Hub-Signature-256": _signature(body),
        "X-GitHub-Event": "pull_request",
        "Content-Type": "application/json",
    }

    first = client.post("/api/v1/webhooks/github", content=body, headers=headers)
    second = client.post("/api/v1/webhooks/github", content=body, headers=headers)

    assert first.status_code == 202
    assert second.status_code == 202
    assert len(queue.enqueued) == 1

    with clean_db.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) FROM review_runs")).scalar_one()
    assert count == 1
