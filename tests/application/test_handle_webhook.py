"""Use case приёма вебхука на фейках портов: без базы, брокера и сети.

Каждый тест сверяет исход — ignored / failure / success — с точными записями
и задачей, а не с «чем-то добавилось». Повторный вызов `run_case` с тем же
`uow`/`queue` — это повторная доставка тем же браузером событий.
"""

from uuid import uuid4

import pytest

from app.application.ports.vcs_gateway import VcsError
from app.application.use_cases.handle_webhook import (
    WebhookOutcome,
    handle_webhook_event,
)
from app.domain.enums import Provider, ReviewRunStatus, TriggerSource

from ..fakes import (
    DIFF,
    INSTALLATION_ID,
    NOW,
    REGISTERED_PROVIDER_ID,
    REPO_FULL_NAME,
    FakeQueue,
    FakeUow,
    FakeVcs,
    a_repository,
    pr_metadata,
    webhook_payload,
)

EXPECTED_DIFF_CALLS = [
    ("diff", REPO_FULL_NAME, 6, INSTALLATION_ID),
    ("metadata", REPO_FULL_NAME, 6, INSTALLATION_ID),
]


def run_case(
    action: str = "opened",
    *,
    vcs_error: VcsError | None = None,
    vcs_diff: str = DIFF,
    vcs_meta=None,
    uow: FakeUow | None = None,
    queue: FakeQueue | None = None,
) -> tuple[WebhookOutcome, FakeUow, FakeVcs, FakeQueue]:
    uow = uow if uow is not None else FakeUow()
    # Повторный вызов с тем же uow — это повторная доставка: репозиторий
    # регистрируется один раз, второй add перезаписал бы его новым id.
    if (Provider.GITHUB, REGISTERED_PROVIDER_ID) not in uow.repositories.stored:
        uow.repositories.add(a_repository())
    vcs = FakeVcs(
        diff=vcs_diff,
        meta=vcs_meta if vcs_meta is not None else pr_metadata(),
        error=vcs_error,
    )
    queue = queue if queue is not None else FakeQueue()
    outcome = handle_webhook_event(
        webhook_payload(action),
        uow,
        vcs,
        queue,
        now=lambda: NOW,
        new_id=uuid4,
    )
    return outcome, uow, vcs, queue


def test_opened_creates_merge_request_run_and_job() -> None:
    outcome, uow, vcs, queue = run_case()
    assert outcome == WebhookOutcome(kind="success", review_run_id=uow.review_runs.runs[0].id)
    assert vcs.calls == EXPECTED_DIFF_CALLS
    assert len(uow.merge_requests.added) == 1
    repo = uow.repositories.stored[(Provider.GITHUB, REGISTERED_PROVIDER_ID)]
    mr = uow.merge_requests.by_number[(repo.id, 6)]
    assert (mr.title, mr.author, mr.head_sha) == (
        "feat: приём вебхуков и VCS-шлюз",
        "ilyassakhanov",
        "a1b2c3d4e5f6789012345678abcdef0123456789",
    )
    run = uow.review_runs.added[0]
    assert run.merge_request_id == mr.id
    assert run.head_sha == mr.head_sha
    assert run.status is ReviewRunStatus.QUEUED
    assert run.trigger is TriggerSource.WEBHOOK
    assert run.base_sha == pr_metadata().base_sha
    assert (run.created_at, run.last_progress_at) == (NOW, NOW)
    assert uow.commits == 1
    assert [job.review_run_id for job in queue.jobs] == [run.id]


def test_opened_job_message_carries_section_4_2_fields() -> None:
    _, _, _, queue = run_case()
    (job,) = queue.jobs
    assert job.repository_full_name == REPO_FULL_NAME
    assert job.repository_provider_id == int(REGISTERED_PROVIDER_ID)
    assert job.pr_number == 6
    assert job.head_sha == "a1b2c3d4e5f6789012345678abcdef0123456789"
    assert job.base_sha == pr_metadata().base_sha
    assert job.action == "opened"
    assert job.priority == 0


def test_base_sha_comes_from_metadata_not_from_payload() -> None:
    """D6: base.sha в payload может быть устаревшим — берём из свежих метаданных."""
    fresh_base = "0000fresh0000fresh0000fresh0000fresh0000f"
    _, _, _, queue = run_case(vcs_meta=pr_metadata(base_sha=fresh_base))
    (job,) = queue.jobs
    assert job.base_sha == fresh_base
    assert job.base_sha != webhook_payload()["pull_request"]["base"]["sha"]


def test_synchronize_shifts_head_sha_of_existing_merge_request() -> None:
    outcome, uow, _, queue = run_case(action="opened")
    (first_run,) = uow.review_runs.added

    outcome, uow, _, queue = run_case(action="synchronize", uow=uow, queue=queue)
    assert outcome.kind == "success"
    repo = uow.repositories.stored[(Provider.GITHUB, REGISTERED_PROVIDER_ID)]
    mr = uow.merge_requests.by_number[(repo.id, 6)]
    assert mr.head_sha == "0987654321abcdef0987654321abcdef09876543"
    assert len(uow.merge_requests.updated) == 1
    new_run = uow.review_runs.added[-1]
    assert new_run.id != first_run.id
    assert new_run.head_sha == mr.head_sha
    assert queue.jobs[-1].head_sha == new_run.head_sha
    assert queue.jobs[-1].action == "synchronize"


def test_reopened_is_ignored_without_any_records() -> None:
    outcome, uow, vcs, queue = run_case(action="reopened")
    assert outcome == WebhookOutcome(kind="ignored")
    assert vcs.calls == []
    assert uow.merge_requests.added == []
    assert uow.review_runs.added == []
    assert queue.jobs == []
    assert uow.commits == 0


@pytest.mark.parametrize("action", ["closed", "assigned", "ready_for_review"])
def test_unknown_actions_are_ignored(action: str) -> None:
    outcome, uow, _, queue = run_case(action=action)
    assert outcome == WebhookOutcome(kind="ignored")
    assert uow.commits == 0
    assert queue.jobs == []


def test_unregistered_repository_is_ignored_without_records() -> None:
    """D5: регистрации на лету нет — вебхук чужого репозитория это пустой игнор."""
    uow = FakeUow()  # ни одного репозитория не зарегистрировано
    vcs = FakeVcs()
    queue = FakeQueue()
    outcome = handle_webhook_event(
        webhook_payload(), uow, vcs, queue, now=lambda: NOW, new_id=uuid4
    )
    assert outcome == WebhookOutcome(kind="ignored")
    assert vcs.calls == []
    assert uow.merge_requests.added == []
    assert uow.review_runs.added == []
    assert queue.jobs == []


def test_vcs_error_yields_failure_and_creates_nothing() -> None:
    outcome, uow, vcs, queue = run_case(vcs_error=VcsError("GitHub API: HTTP 502"))
    assert outcome == WebhookOutcome(kind="failure")
    # Дифф запросили — и на его ошибке обработка прекратилась: метаданные
    # за ним не запрашиваются.
    assert vcs.calls == [EXPECTED_DIFF_CALLS[0]]
    assert uow.merge_requests.added == []
    assert uow.review_runs.added == []
    assert queue.jobs == []
    assert uow.commits == 0


def test_duplicate_delivery_ignores_and_enqueues_nothing() -> None:
    outcome, uow, _, queue = run_case()
    (run,) = uow.review_runs.added

    outcome, uow, _, queue = run_case(uow=uow, queue=queue)
    assert outcome == WebhookOutcome(kind="ignored", review_run_id=run.id)
    assert len(uow.review_runs.added) == 1
    assert len(queue.jobs) == 1


def test_new_head_after_active_run_starts_new_run() -> None:
    """Активный прогон на старом коммите не блокирует новый на новом коммите."""
    outcome, uow, _, queue = run_case(action="opened")
    (old_run,) = uow.review_runs.added
    assert old_run.head_sha != "0987654321abcdef0987654321abcdef09876543"

    outcome, uow, _, queue = run_case(action="synchronize", uow=uow, queue=queue)
    assert outcome.kind == "success"
    assert len(uow.review_runs.added) == 2
    assert old_run.head_sha == "a1b2c3d4e5f6789012345678abcdef0123456789"


def test_diff_is_never_persisted() -> None:
    """D3: дифф транзиентен — ни MR, ни прогон, ни задача его не содержат."""
    lock_diff = (
        "diff --git a/package-lock.json b/package-lock.json\n"
        "--- a/package-lock.json\n+++ b/package-lock.json\n"
        "@@ -1 +1 @@\n-old\n+new\n"
    )
    outcome, uow, _, queue = run_case(vcs_diff=lock_diff)
    assert outcome.kind == "success"
    mr = next(iter(uow.merge_requests.by_number.values()))
    (run,) = uow.review_runs.added
    (job,) = queue.jobs
    for stored in (mr, run, job):
        assert "package-lock.json" not in str(stored)
