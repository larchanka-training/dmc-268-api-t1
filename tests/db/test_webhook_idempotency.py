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
from dataclasses import dataclass, replace
from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from httpx2 import Client, Response
from sqlalchemy import Engine, text

from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import MergeRequest, ReviewJob, ReviewRun
from app.domain.enums import MergeRequestState, ReviewRunStatus, TriggerSource
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db
from ..fakes import (
    INSTALLATION_ID,
    NOW,
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
    # не-2xx GitHub записал бы неудавшейся доставкой.
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


def _finish_only_run(engine: Engine, status: ReviewRunStatus) -> UUID:
    with SqlAlchemyUnitOfWork(engine) as work:
        (run,) = work.review_runs.list_unfinished()
        # Хранилище пускает только переходы жизненного цикла: путь до `completed`
        # идёт через все этапы, а `failed` достижим из `queued` сразу.
        path = (
            [ReviewRunStatus.BUILDING_CONTEXT, ReviewRunStatus.ANALYSING, ReviewRunStatus.PUBLISHING]
            if status is ReviewRunStatus.COMPLETED
            else []
        )
        for step in [*path, status]:
            work.review_runs.update(replace(run, status=step))
            run = replace(run, status=step)
        work.commit()
    return run.id


def _run_count(engine: Engine) -> int:
    with engine.connect() as conn:
        count: int = conn.execute(text("SELECT COUNT(*) FROM review_runs")).scalar_one()
    return count


def test_redelivery_after_completed_run_answers_with_it_against_real_db(flow: Flow) -> None:
    """#75: повтор после завершения — 202 с завершённым прогоном, без второй задачи."""
    post_webhook(flow.client)
    finished_id = _finish_only_run(flow.engine, ReviewRunStatus.COMPLETED)

    again = post_webhook(flow.client)

    assert again.status_code == 202
    assert again.json()["id"] == str(finished_id)
    assert again.json()["status"] == "completed"
    assert len(flow.queue.jobs) == 1
    assert _run_count(flow.engine) == 1


def test_redelivery_after_failed_run_starts_a_new_one_against_real_db(flow: Flow) -> None:
    post_webhook(flow.client)
    failed_id = _finish_only_run(flow.engine, ReviewRunStatus.FAILED)

    again = post_webhook(flow.client)

    assert again.status_code == 202
    assert again.json()["id"] != str(failed_id)
    assert again.json()["status"] == "queued"
    assert len(flow.queue.jobs) == 2
    assert _run_count(flow.engine) == 2


def test_find_reviewed_takes_the_latest_finished_webhook_run(clean_db) -> None:
    """Адаптер: только `webhook`, только терминальный не-`failed`, самый поздний."""
    repository = a_repository()
    merge_request = MergeRequest(
        id=UUID(int=10),
        repository_id=repository.id,
        number=6,
        title="t",
        description="",
        author="octocat",
        source_branch="feat",
        target_branch="main",
        head_sha="h" * 40,
        state=MergeRequestState.OPEN,
        created_at=NOW,
        updated_at=NOW,
    )

    def run(n: int, status: ReviewRunStatus, trigger: TriggerSource, sha: str = "h" * 40) -> ReviewRun:
        moment = NOW + timedelta(minutes=n)
        return ReviewRun(
            id=UUID(int=100 + n),
            merge_request_id=merge_request.id,
            head_sha=sha,
            status=status,
            trigger=trigger,
            last_progress_at=moment,
            created_at=moment,
            updated_at=moment,
        )

    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.repositories.add(repository)
        work.merge_requests.add(merge_request)
        for item in (
            run(1, ReviewRunStatus.COMPLETED, TriggerSource.WEBHOOK),
            run(2, ReviewRunStatus.COMPLETED, TriggerSource.WEBHOOK),
            run(3, ReviewRunStatus.FAILED, TriggerSource.WEBHOOK),
            run(4, ReviewRunStatus.COMPLETED, TriggerSource.MANUAL),
            run(5, ReviewRunStatus.COMPLETED, TriggerSource.WEBHOOK, sha="o" * 40),
            run(6, ReviewRunStatus.QUEUED, TriggerSource.WEBHOOK),
        ):
            work.review_runs.add(item)
        work.commit()

    with SqlAlchemyUnitOfWork(clean_db) as work:
        found = work.review_runs.find_reviewed(merge_request.id, "h" * 40)
        missing = work.review_runs.find_reviewed(merge_request.id, "x" * 40)

    assert found is not None and found.id == UUID(int=102)
    assert missing is None
