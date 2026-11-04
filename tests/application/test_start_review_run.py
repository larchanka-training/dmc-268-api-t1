"""Общая половина вебхука и ручного запуска (`start_review_run`, D1) на фейках портов.

Сценарии гонки, смены ветки и компенсации покрывает `test_handle_webhook.py` — через вебхук,
который зовёт эту функцию. Здесь — то, что вебхук не видит: источник и действие приходят из
`launch`, правило дубля — от вызывающего.
"""

from typing import Any
from uuid import UUID, uuid4

from app.application.ports.unit_of_work import UnitOfWork
from app.application.use_cases.start_review_run import RunLaunch, start_review_run
from app.domain.entities import ReviewRun
from app.domain.enums import ReviewRunStatus, TriggerSource

from ..fakes import (
    INSTALLATION_ID,
    NOW,
    REGISTERED_PROVIDER_ID,
    REPO_FULL_NAME,
    FakeQueue,
    FakeUow,
    FakeVcs,
    a_repository,
    pr_metadata,
)

HEAD = pr_metadata().head_sha
MANUAL = RunLaunch(
    trigger=TriggerSource.MANUAL,
    action="manual",
    pr_number=6,
    head_sha=HEAD,
    installation_id=INSTALLATION_ID,
    repository_full_name=REPO_FULL_NAME,
    repository_provider_id=REGISTERED_PROVIDER_ID,
)


def _no_duplicate(work: UnitOfWork, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
    return None


def _start(uow: FakeUow, queue: FakeQueue, find_duplicate: Any = _no_duplicate) -> Any:
    repository = a_repository()
    uow.repositories.add(repository)
    return start_review_run(
        uow,
        queue,
        FakeVcs(),
        repository,
        MANUAL,
        pr_metadata(),
        find_duplicate=find_duplicate,
        now=lambda: NOW,
        new_id=uuid4,
    )


def test_trigger_and_action_come_from_the_launch() -> None:
    uow, queue = FakeUow(), FakeQueue()

    start = _start(uow, queue)

    assert start.kind == "success"
    (run,) = uow.review_runs.added
    assert run.trigger is TriggerSource.MANUAL
    assert run.status is ReviewRunStatus.QUEUED
    assert run.head_sha == HEAD
    (job,) = queue.jobs
    assert job.action == "manual"
    assert job.event_type == "pull_request"
    assert (job.review_run_id, job.head_sha, job.base_sha) == (run.id, HEAD, pr_metadata().base_sha)
    assert job.installation_id == INSTALLATION_ID


def test_find_duplicate_is_asked_before_the_merge_request_is_updated() -> None:
    """Дубль проверяется до upsert'а: найденный дубль не переписывает запись PR."""
    uow, queue = FakeUow(), FakeQueue()
    _start(uow, queue)
    (first,) = uow.review_runs.added
    seen: list[Any] = []

    def duplicate(work: UnitOfWork, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        seen.append((list(uow.merge_requests.by_number.values()), uow.commits))
        return first

    repository = next(iter(uow.repositories.stored.values()))
    start = start_review_run(
        uow,
        queue,
        FakeVcs(),
        repository,
        MANUAL,
        pr_metadata(),
        find_duplicate=duplicate,
        now=lambda: NOW,
        new_id=uuid4,
    )

    assert start.kind == "duplicate"
    assert start.run == first
    (merge_requests, commits) = seen[0]
    assert len(merge_requests) == 1
    assert commits == 1  # только первая запись: дубль ничего не коммитит
    assert uow.commits == 1
    assert len(queue.jobs) == 1


def test_a_new_pull_request_is_not_asked_for_a_duplicate() -> None:
    """Записи PR нет — дубля быть не может, правило вызывающего не зовётся."""
    calls: list[str] = []

    def duplicate(work: UnitOfWork, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        calls.append(head_sha)
        return None

    start = _start(FakeUow(), FakeQueue(), duplicate)

    assert start.kind == "success"
    assert calls == []
