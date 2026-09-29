"""Контракт вебхука: HMAC, идемпотентность, постановка задачи. Без БД и брокера."""

import hashlib
import hmac
import json
from collections.abc import Iterator

from fastapi.testclient import TestClient

from app.api.dependencies import get_job_queue, get_unit_of_work
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import ReviewJob
from app.domain.enums import ReviewRunStatus

from .fakes.unit_of_work import FakeUnitOfWork

SECRET = "test-secret"

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret=SECRET,
)

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


def _signature(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _client(uow: FakeUnitOfWork, job_queue: FakeJobQueue) -> TestClient:
    app = create_app(SETTINGS)

    def override_uow() -> Iterator[FakeUnitOfWork]:
        yield uow

    app.dependency_overrides[get_unit_of_work] = override_uow
    app.dependency_overrides[get_job_queue] = lambda: job_queue
    return TestClient(app)


def test_valid_signature_is_accepted_and_enqueues_a_job() -> None:
    uow = FakeUnitOfWork()
    queue = FakeJobQueue()
    client = _client(uow, queue)
    body = json.dumps(PAYLOAD).encode()

    response = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _signature(body),
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 202
    assert len(queue.enqueued) == 1
    assert queue.enqueued[0].head_sha == "abc123def"
    runs = uow.review_runs.list_unfinished()
    assert len(runs) == 1
    assert runs[0].status == ReviewRunStatus.QUEUED
    assert runs[0].id == queue.enqueued[0].id


def test_missing_signature_is_rejected() -> None:
    uow = FakeUnitOfWork()
    queue = FakeJobQueue()
    client = _client(uow, queue)
    body = json.dumps(PAYLOAD).encode()

    response = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={"X-GitHub-Event": "pull_request", "Content-Type": "application/json"},
    )

    assert response.status_code == 401
    assert queue.enqueued == []
    assert uow.review_runs.list_unfinished() == []


def test_wrong_signature_is_rejected() -> None:
    uow = FakeUnitOfWork()
    queue = FakeJobQueue()
    client = _client(uow, queue)
    body = json.dumps(PAYLOAD).encode()

    response = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _signature(body, secret="wrong-secret"),
            "X-GitHub-Event": "pull_request",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 401
    assert queue.enqueued == []


def test_non_pull_request_event_is_ignored_not_errored() -> None:
    uow = FakeUnitOfWork()
    queue = FakeJobQueue()
    client = _client(uow, queue)
    body = json.dumps({"zen": "keep it logically awesome"}).encode()

    response = client.post(
        "/api/v1/webhooks/github",
        content=body,
        headers={
            "X-Hub-Signature-256": _signature(body),
            "X-GitHub-Event": "ping",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 202
    assert queue.enqueued == []


def test_repeated_delivery_does_not_create_a_second_run() -> None:
    uow = FakeUnitOfWork()
    queue = FakeJobQueue()
    client = _client(uow, queue)
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
    assert len(uow.review_runs.list_unfinished()) == 1
