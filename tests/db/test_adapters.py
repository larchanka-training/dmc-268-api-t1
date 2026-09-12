"""What the adapters do, and what they refuse."""

import datetime as dt
from dataclasses import replace

import pytest

from app.domain.entities import (
    ContextPayload,
    DiffAnchor,
    Finding,
    Hunk,
    MergeRequest,
    Repository,
    ReviewRun,
)
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.ids import new_id
from app.domain.lifecycle import advance
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

NOW = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.UTC)
LATER = NOW + dt.timedelta(minutes=5)


@pytest.fixture
def uow(clean_db):
    return SqlAlchemyUnitOfWork(clean_db)


def a_repository() -> Repository:
    return Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id="1",
        full_name="acme/api",
        default_branch="main",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    )


def a_merge_request(repo_id) -> MergeRequest:
    return MergeRequest(
        id=new_id(),
        repository_id=repo_id,
        number=1,
        title="t",
        description="",
        author="a",
        source_branch="feat",
        target_branch="main",
        head_sha="abc123",
        state="open",
        created_at=NOW,
        updated_at=NOW,
    )


def a_run(mr_id) -> ReviewRun:
    return ReviewRun(
        id=new_id(),
        merge_request_id=mr_id,
        head_sha="abc123",
        status=ReviewRunStatus.QUEUED,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def test_entities_round_trip_through_the_ports(uow) -> None:
    repo = a_repository()
    with uow as work:
        work.repositories.add(repo)
        work.commit()
    with uow as work:
        stored = work.repositories.find_by_provider(Provider.GITHUB, "1")
    assert stored is not None
    assert stored.full_name == repo.full_name
    assert stored.id == repo.id


def test_a_failed_unit_of_work_rolls_everything_back(uow) -> None:
    with uow as work:
        work.repositories.add(a_repository())
        # leaving the block without commit must discard the write
    with uow as work:
        assert work.repositories.find_by_provider(Provider.GITHUB, "1") is None


def test_find_active_ignores_finished_runs(uow) -> None:
    repo, mr = a_repository(), None
    with uow as work:
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run = a_run(mr.id)
        work.review_runs.add(run)
        work.commit()
    with uow as work:
        assert work.review_runs.find_active(mr.id, "abc123") is not None
        from dataclasses import replace

        work.review_runs.update(
            replace(
                work.review_runs.find_active(mr.id, "abc123"),
                status=ReviewRunStatus.BUILDING_CONTEXT,
                last_progress_at=LATER,
            )
        )
        work.commit()
    with uow as work:
        active = work.review_runs.find_active(mr.id, "abc123")
        assert active.status is ReviewRunStatus.BUILDING_CONTEXT
        assert active.last_progress_at == LATER


def test_the_adapter_refuses_an_illegal_transition(uow) -> None:
    from dataclasses import replace

    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run = a_run(mr.id)
        work.review_runs.add(run)
        work.commit()
    with uow as work:
        stored = work.review_runs.get(run.id)
        with pytest.raises(ValueError, match="cannot move"):
            work.review_runs.update(replace(stored, status=ReviewRunStatus.COMPLETED))


def test_a_finding_outside_the_diff_is_refused_and_counted(uow) -> None:
    hunks = [Hunk(file_path="app/main.py", changed_new_lines=frozenset({10}))]
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run = a_run(mr.id)
        work.review_runs.add(run)
        work.commit()

    outside = Finding(
        id=new_id(),
        review_run_id=run.id,
        anchor=DiffAnchor("app/main.py", DiffSide.NEW, new_line=999),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.HIGH,
        message="m",
        created_at=NOW,
        updated_at=NOW,
    )
    with uow as work:
        with pytest.raises(ValueError, match="outside the changed lines"):
            work.findings.add_validated(outside, hunks, LATER)
        work.commit()

    with uow as work:
        assert work.findings.list_for_run(run.id) == []
        assert work.review_runs.get(run.id).rejected_findings == 1


def test_a_finding_inside_the_diff_is_stored(uow) -> None:
    hunks = [Hunk(file_path="app/main.py", changed_new_lines=frozenset({10}))]
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run = a_run(mr.id)
        work.review_runs.add(run)
        work.commit()
    inside = Finding(
        id=new_id(),
        review_run_id=run.id,
        anchor=DiffAnchor("app/main.py", DiffSide.NEW, new_line=10),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.HIGH,
        message="m",
        created_at=NOW,
        updated_at=NOW,
    )
    with uow as work:
        work.findings.add_validated(inside, hunks, LATER)
        work.commit()
    with uow as work:
        stored = work.findings.list_for_run(run.id)
        assert len(stored) == 1
        assert stored[0].anchor.new_line == 10


def a_context_payload(run_id, *, chunk_index: int, digest: str) -> ContextPayload:
    return ContextPayload(
        id=new_id(),
        review_run_id=run_id,
        chunk_index=chunk_index,
        tiers=("diff",),
        file_paths=("app/main.py",),
        token_count=10,
        content_sha256=digest,
        body={"text": "x"},
        created_at=NOW,
        updated_at=NOW,
    )


def seeded(work):
    """A repository, a change request and a queued run, all committed."""
    repo = a_repository()
    work.repositories.add(repo)
    mr = a_merge_request(repo.id)
    work.merge_requests.add(mr)
    run = a_run(mr.id)
    work.review_runs.add(run)
    work.commit()
    return repo, mr, run


def test_advancing_a_run_does_not_undo_a_recorded_rejection(uow) -> None:
    """The counter lives on the row; the caller's entity never learns about it."""
    hunks = [Hunk(file_path="app/main.py", changed_new_lines=frozenset({10}))]
    with uow as work:
        _, _, run = seeded(work)

    outside = Finding(
        id=new_id(),
        review_run_id=run.id,
        anchor=DiffAnchor("app/main.py", DiffSide.NEW, new_line=999),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.HIGH,
        message="m",
        created_at=NOW,
        updated_at=NOW,
    )
    with uow as work:
        with pytest.raises(ValueError, match="outside the changed lines"):
            work.findings.add_validated(outside, hunks, LATER)
        # `run` is the stale entity the caller has been holding all along
        work.review_runs.update(
            advance(run, ReviewRunStatus.BUILDING_CONTEXT, LATER).unwrap()
        )
        work.commit()

    with uow as work:
        stored = work.review_runs.get(run.id)
    assert stored.rejected_findings == 1
    assert stored.status is ReviewRunStatus.BUILDING_CONTEXT


def test_a_run_is_inserted_with_the_counts_it_already_carries(uow) -> None:
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        work.commit()
    requeued = replace(
        a_run(mr.id),
        rejected_findings=3,
        model="qwen2.5-coder",
        tokens_used=1200,
        duration_seconds=42,
    )
    with uow as work:
        work.review_runs.add(requeued)
        work.commit()
    with uow as work:
        stored = work.review_runs.get(requeued.id)
    assert stored.rejected_findings == 3
    assert stored.model == "qwen2.5-coder"
    assert stored.tokens_used == 1200
    assert stored.duration_seconds == 42


def test_retargeting_a_change_request_reaches_storage(uow) -> None:
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        work.commit()
    retargeted = replace(mr, source_branch="feat/renamed", target_branch="release/1.x")
    with uow as work:
        work.merge_requests.update(retargeted)
        work.commit()
    with uow as work:
        stored = work.merge_requests.find_by_number(repo.id, mr.number)
    assert stored.target_branch == "release/1.x"
    assert stored.source_branch == "feat/renamed"


def test_find_by_digest_is_stable_across_calls(uow) -> None:
    """Several runs can share a digest; the lookup must not pick at random."""
    with uow as work:
        _, _, run = seeded(work)
    with uow as work:
        for index in range(3):
            work.context_payloads.add(
                a_context_payload(run.id, chunk_index=index, digest="same-digest")
            )
        work.commit()
    with uow as work:
        answers = {work.context_payloads.find_by_digest("same-digest").id for _ in range(5)}
    assert len(answers) == 1


def test_a_payload_body_is_a_copy_not_a_handle_on_the_row(uow) -> None:
    with uow as work:
        _, _, run = seeded(work)
    payload = a_context_payload(run.id, chunk_index=0, digest="d1")
    with uow as work:
        work.context_payloads.add(payload)
        work.commit()

    with uow as work:
        loaded = work.context_payloads.find_by_digest("d1")
        loaded.body["text"] = "mutated"
        work.commit()

    with uow as work:
        assert work.context_payloads.find_by_digest("d1").body == {"text": "x"}
