"""Интеграционный E2E: подписанный вебхук → HTTP → база → очередь.

Живого GitHub и брокера нет: `VcsGateway` — фейк, отдающий записанный
`sample.diff`, очередь — `FakeQueue`, хранящий сущности `ReviewJob` как есть.
Всё между ними настоящее: эндпоинт, use case и `SqlAlchemyUnitOfWork` на
тестовой базе. Поля задачи сверяются по сущности; wire-формат сообщения §4.2,
топология и свойства AMQP — юнит- и интеграционные тесты `tests/queue/`.

Без `TEST_DATABASE_URL` набор пропускается — как остальные интеграционные.
"""

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx import Client, Response
from sqlalchemy import Engine

from app.api.factory import create_app
from app.application.ports import JobQueue, UnitOfWork, VcsGateway
from app.config import Settings
from app.domain.entities import MergeRequest, Repository, ReviewRun
from app.domain.enums import ReviewRunStatus, TriggerSource
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db
from ..fakes import (
    INSTALLATION_ID,
    REPO_FULL_NAME,
    FakeQueue,
    FakeVcs,
    a_repository,
    pr_metadata,
    webhook_payload,
)

pytestmark = [pytest.mark.integration, requires_db]

FIXTURES = Path(__file__).parent.parent / "fixtures"
SAMPLE_DIFF = (FIXTURES / "sample.diff").read_text(encoding="utf-8")
SECRET = "test-webhook-signature-secret"
SIGNATURE_HEADER = "X-Hub-Signature-256"
EVENT_HEADER = "X-GitHub-Event"
PR_NUMBER = 6
HEAD_SHA = "a1b2c3d4e5f6789012345678abcdef0123456789"
BASE_SHA = pr_metadata().base_sha


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@dataclass
class E2EContainer:
    """Настоящий адаптер базы, фейковые VCS и очередь: сеть не нужна."""

    engine: Engine
    settings: Settings
    vcs: FakeVcs
    queue: FakeQueue

    def unit_of_work(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)

    def vcs_gateway(self) -> VcsGateway:
        return self.vcs

    def job_queue(self) -> JobQueue:
        return self.queue


@dataclass(frozen=True)
class Flow:
    client: TestClient
    engine: Engine
    repository: Repository
    vcs: FakeVcs
    queue: FakeQueue


@pytest.fixture
def flow(clean_db) -> Flow:
    repository = a_repository()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.repositories.add(repository)
        work.commit()

    settings = Settings(
        database_url=str(clean_db.url),
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret=SECRET,
    )
    vcs = FakeVcs(diff=SAMPLE_DIFF)
    queue = FakeQueue()
    container = E2EContainer(
        engine=clean_db,
        settings=settings,
        vcs=vcs,
        queue=queue,
    )
    app = create_app(settings)
    app.state.container = container
    return Flow(
        client=TestClient(app),
        engine=clean_db,
        repository=repository,
        vcs=vcs,
        queue=queue,
    )


def post_webhook(
    client: Client,
    action: str = "opened",
    *,
    signature: str | None = "auto",
    event: str | None = "pull_request",
) -> Response:
    body = json.dumps(webhook_payload(action)).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers[SIGNATURE_HEADER] = sign(body)
    elif signature is not None:
        headers[SIGNATURE_HEADER] = signature
    if event is not None:
        headers[EVENT_HEADER] = event
    return client.post("/api/v1/webhooks/github", content=body, headers=headers)


def stored_state(
    engine: Engine, repository_id: UUID
) -> tuple[MergeRequest | None, list[ReviewRun]]:
    """Записи после запроса: отдельная сессия видит только закоммиченное."""
    with SqlAlchemyUnitOfWork(engine) as work:
        merge_request = work.merge_requests.find_by_number(repository_id, PR_NUMBER)
        runs = work.review_runs.list_unfinished()
    return merge_request, runs


def test_opened_creates_records_and_publishes_section_4_2_message(flow: Flow) -> None:
    response = post_webhook(flow.client, "opened")

    assert response.status_code == 202
    job = response.json()
    merge_request, runs = stored_state(flow.engine, flow.repository.id)
    assert merge_request is not None
    # Заголовок и автор — из свежих метаданных VCS, не из payload'а.
    assert (merge_request.title, merge_request.author) == (
        pr_metadata().title,
        pr_metadata().author,
    )
    (run,) = runs
    assert run.head_sha == HEAD_SHA
    assert run.status is ReviewRunStatus.QUEUED
    assert run.trigger is TriggerSource.WEBHOOK

    # Тело 202 — публичная проекция ReviewJob из openapi.yaml, camelCase.
    assert job == {
        "id": str(run.id),
        "provider": "github",
        "providerRepositoryId": str(flow.repository.provider_id),
        "pullRequestNumber": PR_NUMBER,
        "trigger": "webhook",
        "status": "queued",
        "headCommitSha": HEAD_SHA,
        "baseCommitSha": BASE_SHA,
        "findingsCount": 0,
        "rejectedFindings": 0,
        "createdAt": run.created_at.isoformat(),
        "updatedAt": run.updated_at.isoformat(),
        "lastProgressAt": run.last_progress_at.isoformat(),
        "error": None,
    }

    # Задача в очереди — сущность с полями §4.2; id задачи — id прогона.
    (enqueued,) = flow.queue.jobs
    assert enqueued.review_run_id == run.id
    assert enqueued.id != run.id
    assert enqueued.event_type == "pull_request"
    assert enqueued.action == "opened"
    assert enqueued.repository_full_name == REPO_FULL_NAME
    assert enqueued.repository_provider_id == flow.repository.provider_id
    assert enqueued.pull_request_number == PR_NUMBER
    assert enqueued.head_sha == HEAD_SHA
    assert enqueued.base_sha == BASE_SHA
    assert enqueued.installation_id == INSTALLATION_ID


def test_vcs_is_called_for_metadata_then_diff(flow: Flow) -> None:
    """Метаданные до диффа: base_sha берётся из них, дифф — той же пары SHA,
    что уйдёт в сообщение. Сбой любого из них даёт 502 до создания записей."""
    post_webhook(flow.client, "opened")

    assert flow.vcs.calls == [
        ("metadata", REPO_FULL_NAME, PR_NUMBER, INSTALLATION_ID),
        ("diff", REPO_FULL_NAME, BASE_SHA, HEAD_SHA, INSTALLATION_ID),
    ]


@pytest.mark.parametrize("signature", [None, "sha256=" + "0" * 64])
def test_rejected_signature_leaves_no_records(flow: Flow, signature: str | None) -> None:
    response = post_webhook(flow.client, "opened", signature=signature)

    assert response.status_code == 401
    merge_request, runs = stored_state(flow.engine, flow.repository.id)
    assert merge_request is None
    assert runs == []
    assert flow.queue.jobs == []
    assert flow.vcs.calls == []


def test_reopened_is_ignored_without_records(flow: Flow) -> None:
    response = post_webhook(flow.client, "reopened")

    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    merge_request, runs = stored_state(flow.engine, flow.repository.id)
    assert merge_request is None
    assert runs == []
    assert flow.queue.jobs == []
    assert flow.vcs.calls == []


def test_foreign_event_type_is_ignored_without_records(flow: Flow) -> None:
    """Подписанный `pull_request_target` с pull-request-совместимым телом
    не создаёт ни записей, ни задач: тип события — только из заголовка."""
    response = post_webhook(flow.client, "opened", event="pull_request_target")

    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    merge_request, runs = stored_state(flow.engine, flow.repository.id)
    assert merge_request is None
    assert runs == []
    assert flow.queue.jobs == []
    assert flow.vcs.calls == []
