"""Интеграционный E2E: подписанный вебхук → HTTP → база → очередь.

Живого GitHub и брокера нет: `VcsGateway` — фейк, отдающий записанный
`sample.diff`, канал RabbitMQ — двойник, записывающий публикации. Всё между
ними настоящее: эндпоинт, use case, `SqlAlchemyUnitOfWork` на тестовой базе
и `PikaJobQueue`, который сериализует задачу по §4.2 `SYSTEM_DESIGN.md`.

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
from app.application.ports import CacheStore, JobQueue, UnitOfWork, VcsGateway
from app.config import Settings
from app.domain.entities import MergeRequest, Repository, ReviewRun
from app.domain.enums import ReviewRunStatus, TriggerSource
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.queue.rabbitmq import QUEUE_NAME, PikaJobQueue

from ..conftest import requires_db
from ..fakes import (
    INSTALLATION_ID,
    REPO_FULL_NAME,
    FakeCacheStore,
    FakeChannel,
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
PR_NUMBER = 6
HEAD_SHA = "a1b2c3d4e5f6789012345678abcdef0123456789"
BASE_SHA = pr_metadata().base_sha


def sign(body: bytes, secret: str = SECRET) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@dataclass
class E2EContainer:
    """Настоящие адаптеры базы и очереди, фейковые VCS и кэш: сеть не нужна."""

    engine: Engine
    settings: Settings
    vcs: FakeVcs
    queue: PikaJobQueue
    cache: FakeCacheStore

    def unit_of_work(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)

    def vcs_gateway(self) -> VcsGateway:
        return self.vcs

    def job_queue(self) -> JobQueue:
        return self.queue

    def cache_store(self) -> CacheStore:
        return self.cache


@dataclass(frozen=True)
class Flow:
    client: TestClient
    engine: Engine
    repository: Repository
    vcs: FakeVcs
    channel: FakeChannel
    cache: FakeCacheStore


@pytest.fixture
def flow(clean_db) -> Flow:
    repository = a_repository()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.repositories.add(repository)
        work.commit()

    settings = Settings(
        database_url=str(clean_db.url),
        github_webhook_secret=SECRET,
    )
    vcs = FakeVcs(diff=SAMPLE_DIFF)
    channel = FakeChannel()
    cache = FakeCacheStore()
    container = E2EContainer(
        engine=clean_db,
        settings=settings,
        vcs=vcs,
        queue=PikaJobQueue(url="", channel=channel),
        cache=cache,
    )
    app = create_app(settings)
    app.state.container = container
    return Flow(
        client=TestClient(app),
        engine=clean_db,
        repository=repository,
        vcs=vcs,
        channel=channel,
        cache=cache,
    )


def post_webhook(
    client: Client,
    action: str = "opened",
    *,
    signature: str | None = "auto",
) -> Response:
    body = json.dumps(webhook_payload(action)).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if signature == "auto":
        headers[SIGNATURE_HEADER] = sign(body)
    elif signature is not None:
        headers[SIGNATURE_HEADER] = signature
    return client.post("/api/webhooks/github", content=body, headers=headers)


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

    (published,) = flow.channel.published
    body = json.loads(published.body)
    assert UUID(body.pop("job_id")).version == 7
    assert body == {
        "event_type": "pull_request",
        "action": "opened",
        "repository": {
            "full_name": REPO_FULL_NAME,
            "id": int(flow.repository.provider_id),
        },
        "pull_request": {
            "number": PR_NUMBER,
            "head_sha": HEAD_SHA,
            "base_sha": BASE_SHA,
        },
    }
    # Приоритет открытого PR — дефолтный, выведен адаптером из action.
    assert published.properties.priority == 0
    assert "priority" not in body
    assert flow.channel.declared == [
        {
            "queue": QUEUE_NAME,
            "durable": True,
            "arguments": {"x-max-priority": 10},
        }
    ]
    # Дифф не выброшен: он в кэше по ключу доставки — воркер не потянет
    # его у GitHub третий раз (§4.3).
    assert flow.cache.entries == {
        f"diff:{REPO_FULL_NAME}:{PR_NUMBER}:{HEAD_SHA}": SAMPLE_DIFF
    }


def test_vcs_is_called_for_diff_then_metadata(flow: Flow) -> None:
    """Дифф запрашивается до метаданных: его сбой даёт 502 до создания записей."""
    post_webhook(flow.client, "opened")

    assert flow.vcs.calls == [
        ("diff", REPO_FULL_NAME, PR_NUMBER, INSTALLATION_ID),
        ("metadata", REPO_FULL_NAME, PR_NUMBER, INSTALLATION_ID),
    ]


@pytest.mark.parametrize("signature", [None, "sha256=" + "0" * 64])
def test_rejected_signature_leaves_no_records(flow: Flow, signature: str | None) -> None:
    response = post_webhook(flow.client, "opened", signature=signature)

    assert response.status_code == 401
    merge_request, runs = stored_state(flow.engine, flow.repository.id)
    assert merge_request is None
    assert runs == []
    assert flow.channel.published == []
    assert flow.vcs.calls == []


def test_reopened_is_ignored_without_records(flow: Flow) -> None:
    response = post_webhook(flow.client, "reopened")

    assert response.status_code == 202
    assert response.json() == {"status": "ignored"}
    merge_request, runs = stored_state(flow.engine, flow.repository.id)
    assert merge_request is None
    assert runs == []
    assert flow.channel.published == []
    assert flow.vcs.calls == []
