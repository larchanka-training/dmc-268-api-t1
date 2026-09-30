"""Приём запроса на ревью — use case без HTTP, базы и брокера.

Доказывает, что сценарий вызывается не только из вебхука: те же проверки идут
с `TriggerSource.MANUAL`.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from itertools import count
from uuid import UUID

import pytest

from app.application.review_intake import (
    IntakeOutcome,
    ReviewRequest,
    accept_review_request,
)
from app.domain.entities import ReviewJob
from app.domain.enums import MergeRequestState, Provider, ReviewRunStatus, TriggerSource

from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


def _request(
    trigger: TriggerSource = TriggerSource.MANUAL, **overrides: object
) -> ReviewRequest:
    fields: dict[str, object] = {
        "provider": Provider.GITHUB,
        "trigger": trigger,
        "event_type": "manual",
        "action": "requested",
        "repository_provider_id": "987654",
        "repository_full_name": "owner/repo",
        "default_branch": "main",
        "number": 42,
        "title": "Fix",
        "description": "",
        "author": "octocat",
        "source_branch": "feature",
        "target_branch": "main",
        "head_sha": "abc123",
        "base_sha": "fed654",
        "state": MergeRequestState.OPEN,
    }
    fields.update(overrides)
    return ReviewRequest(**fields)  # type: ignore[arg-type]


class _Queue:
    def __init__(self) -> None:
        self.jobs: list[ReviewJob] = []

    def enqueue(self, job: ReviewJob) -> None:
        self.jobs.append(job)


def _ids() -> Callable[[], UUID]:
    counter = count(1)
    return lambda: UUID(int=next(counter))


def test_a_manual_request_creates_a_run_with_its_trigger_and_enqueues_it() -> None:
    uow, queue = FakeUnitOfWork(), _Queue()

    outcome = accept_review_request(
        _request(), uow=uow, job_queue=queue, now=NOW, new_id=_ids()
    )

    assert outcome is IntakeOutcome.ENQUEUED
    [run] = uow.review_runs.list_unfinished()
    assert run.trigger is TriggerSource.MANUAL
    assert run.status is ReviewRunStatus.QUEUED
    assert [job.id for job in queue.jobs] == [run.id]


def test_a_second_request_for_the_same_commit_is_a_duplicate() -> None:
    uow, queue = FakeUnitOfWork(), _Queue()
    new_id = _ids()
    accept_review_request(_request(), uow=uow, job_queue=queue, now=NOW, new_id=new_id)

    outcome = accept_review_request(
        _request(), uow=uow, job_queue=queue, now=NOW, new_id=new_id
    )

    assert outcome is IntakeOutcome.DUPLICATE
    assert len(queue.jobs) == 1
    assert len(uow.review_runs.list_unfinished()) == 1


def test_a_missing_title_does_not_erase_the_stored_one() -> None:
    uow, queue = FakeUnitOfWork(), _Queue()
    new_id = _ids()
    accept_review_request(_request(), uow=uow, job_queue=queue, now=NOW, new_id=new_id)

    accept_review_request(
        _request(title="", head_sha="def456"),
        uow=uow,
        job_queue=queue,
        now=NOW,
        new_id=new_id,
    )

    repository = uow.repositories.find_by_provider(Provider.GITHUB, "987654")
    assert repository is not None
    merge_request = uow.merge_requests.find_by_number(repository.id, 42)
    assert merge_request is not None
    assert merge_request.title == "Fix"
    assert merge_request.head_sha == "def456"


def test_a_queue_failure_fails_the_run_and_reraises() -> None:
    uow = FakeUnitOfWork()

    class _Broken:
        def enqueue(self, job: ReviewJob) -> None:
            raise ConnectionError("брокер недоступен")

    with pytest.raises(ConnectionError):
        accept_review_request(
            _request(), uow=uow, job_queue=_Broken(), now=NOW, new_id=_ids()
        )

    assert uow.review_runs.list_unfinished() == []
