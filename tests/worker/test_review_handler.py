"""Обработчик воркера: разбор сообщения очереди + `run_review`. Без БД и брокера."""

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.ports.llm_gateway import LlmReviewResult
from app.domain.entities import ReviewJob, ReviewRun
from app.domain.enums import ReviewRunStatus, TriggerSource
from app.infrastructure.queue.rabbitmq import to_wire_message
from app.worker.factory import build_review_handler

from ..fakes.unit_of_work import FakeUnitOfWork

JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    event_type="pull_request",
    action="opened",
    repository_provider_id="987654",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="abc123def",
    base_sha="fed654cba",
)


class _StubGateway:
    def review(self, context: object) -> LlmReviewResult:
        return LlmReviewResult(model="stub-llm", tokens_used=0, findings=())


class _Deps:
    """Заглушка вместо `Container`: только то, что нужно обработчику."""

    def __init__(self, uow: FakeUnitOfWork) -> None:
        self._uow = uow

    def unit_of_work(self) -> FakeUnitOfWork:
        return self._uow

    def llm_gateway(self) -> _StubGateway:
        return _StubGateway()


def test_handler_parses_the_message_and_drives_the_run_to_completed() -> None:
    now = datetime(2026, 9, 29, tzinfo=UTC)
    uow = FakeUnitOfWork()
    uow.review_runs.add(
        ReviewRun(
            id=JOB.review_run_id,
            merge_request_id=UUID(int=3),
            head_sha=JOB.head_sha,
            status=ReviewRunStatus.QUEUED,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")

    handler = build_review_handler(_Deps(uow))
    handler(body)

    run = uow.review_runs.get(JOB.review_run_id)
    assert run is not None
    assert run.status == ReviewRunStatus.COMPLETED
    assert run.model == "stub-llm"


def test_handler_logs_both_ids_when_the_run_fails(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Прогона нет — обработка падает; в логе должны быть и задача, и прогон.
    # Интеграционные тесты миграций вызывают `fileConfig` из `alembic/env.py`,
    # а он выключает уже созданные логгеры — без этого тест зависит от порядка.
    monkeypatch.setattr(logging.getLogger("app.worker.factory"), "disabled", False)
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")
    handler = build_review_handler(_Deps(FakeUnitOfWork()))

    with caplog.at_level(logging.ERROR), pytest.raises(LookupError):
        handler(body)

    [record] = caplog.records
    assert str(JOB.id) in record.getMessage()
    assert str(JOB.review_run_id) in record.getMessage()
