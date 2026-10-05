"""Контракт `GET /api/v1/reviews/{id}`. Без БД."""

from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.api.dependencies import get_unit_of_work
from app.api.factory import create_app
from app.config import Settings
from app.domain.entities import DiffAnchor, Finding, ReviewRun
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.ids import new_id

from .fakes.unit_of_work import FakeUnitOfWork

SETTINGS = Settings(
    database_url="postgresql+psycopg://test:test@localhost/test",
    rabbitmq_url="amqp://guest:guest@localhost//",
    github_webhook_secret="test-secret",
)

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def _client(uow: FakeUnitOfWork) -> TestClient:
    app = create_app(SETTINGS)

    def override_uow() -> Iterator[FakeUnitOfWork]:
        yield uow

    app.dependency_overrides[get_unit_of_work] = override_uow
    return TestClient(app)


def _run(**overrides: object) -> ReviewRun:
    fields = {
        "id": new_id(),
        "merge_request_id": new_id(),
        "head_sha": "abc123",
        "status": ReviewRunStatus.COMPLETED,
        "trigger": TriggerSource.WEBHOOK,
        "last_progress_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
        "model": "stub-llm",
        "tokens_used": 0,
    }
    fields.update(overrides)
    return ReviewRun(**fields)  # type: ignore[arg-type]


def test_returns_a_run_with_its_findings() -> None:
    uow = FakeUnitOfWork()
    run = _run()
    uow.review_runs.add(run)
    uow.findings.add(
        Finding(
            id=new_id(),
            review_run_id=run.id,
            anchor=DiffAnchor(file_path="a.py", side=DiffSide.NEW, new_line=1),
            category=FindingCategory.READABILITY,
            severity=FindingSeverity.LOW,
            message="сообщение",
            created_at=NOW,
            updated_at=NOW,
        )
    )
    client = _client(uow)

    response = client.get(f"/api/v1/reviews/{run.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["model"] == "stub-llm"
    assert len(body["findings"]) == 1
    assert body["findings"][0]["message"] == "сообщение"


def test_a_run_with_no_findings_returns_an_empty_list() -> None:
    uow = FakeUnitOfWork()
    run = _run()
    uow.review_runs.add(run)
    client = _client(uow)

    response = client.get(f"/api/v1/reviews/{run.id}")

    assert response.status_code == 200
    assert response.json()["findings"] == []


def test_unknown_run_is_404() -> None:
    uow = FakeUnitOfWork()
    client = _client(uow)

    response = client.get(f"/api/v1/reviews/{new_id()}")

    assert response.status_code == 404
