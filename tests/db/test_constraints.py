"""What the schema refuses.

Each test states a rule from the data-model spec and proves the database
enforces it, rather than trusting that application code will remember to.
"""

import datetime as dt

import pytest
from sqlalchemy.exc import IntegrityError

from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.ids import new_id
from app.infrastructure.db.models import (
    ContextPayloadRow,
    FindingRow,
    MergeRequestRow,
    PublishedCommentRow,
    RepositoryRow,
    ReviewRunRow,
)

from ..conftest import requires_db

pytestmark = [pytest.mark.integration, requires_db]

NOW = dt.datetime(2026, 9, 9, 12, 0, tzinfo=dt.UTC)


def make_repo(session, *, provider=Provider.GITHUB, provider_id="1", name="acme/api", flush=True):
    row = RepositoryRow(
        id=new_id(),
        provider=provider,
        provider_id=provider_id,
        full_name=name,
        default_branch="main",
        auto_review_enabled=True,
    )
    session.add(row)
    if flush:
        session.flush()
    return row


def make_mr(session, repo, *, number=1, head="abc123", flush=True):
    row = MergeRequestRow(
        id=new_id(),
        repository_id=repo.id,
        number=number,
        title="t",
        description="",
        author="a",
        source_branch="feat",
        target_branch="main",
        head_sha=head,
        state="open",
    )
    session.add(row)
    if flush:
        session.flush()
    return row


def make_run(session, mr, *, head="abc123", status=ReviewRunStatus.QUEUED, flush=True):
    row = ReviewRunRow(
        id=new_id(),
        merge_request_id=mr.id,
        head_sha=head,
        status=status,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
    )
    session.add(row)
    if flush:
        session.flush()
    return row


def test_a_repository_is_unique_per_provider_and_provider_id(session) -> None:
    make_repo(session, provider_id="42")
    make_repo(session, provider_id="42", flush=False)
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_same_full_name_on_two_providers_is_allowed(session) -> None:
    make_repo(session, provider=Provider.GITHUB, provider_id="1", name="acme/api")
    make_repo(session, provider=Provider.GITLAB, provider_id="1", name="acme/api")
    session.flush()


def test_deleting_a_repository_takes_its_merge_requests(session) -> None:
    repo = make_repo(session)
    make_mr(session, repo)
    session.delete(repo)
    session.flush()
    assert session.query(MergeRequestRow).count() == 0


def test_a_merge_request_number_is_unique_within_a_repository(session) -> None:
    repo = make_repo(session)
    make_mr(session, repo, number=7)
    make_mr(session, repo, number=7, flush=False)
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_run_round_trips_from_queued_to_completed_with_its_cost(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    run.status = ReviewRunStatus.COMPLETED
    run.model = "qwen2.5-coder:14b"
    run.tokens_used = 12_345
    run.duration_seconds = 42.5
    session.flush()
    stored = session.get(ReviewRunRow, run.id)
    assert stored.status is ReviewRunStatus.COMPLETED
    assert (stored.model, stored.tokens_used, stored.duration_seconds) == (
        "qwen2.5-coder:14b",
        12_345,
        42.5,
    )


def test_timestamps_come_back_time_zone_aware(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    session.refresh(run)
    assert run.created_at.tzinfo is not None
    assert run.last_progress_at.tzinfo is not None


def test_a_second_active_run_for_the_same_commit_is_refused(session) -> None:
    mr = make_mr(session, make_repo(session))
    make_run(session, mr, head="deadbeef")
    make_run(session, mr, head="deadbeef", flush=False)
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_re_review_is_allowed_once_the_first_run_finished(session) -> None:
    mr = make_mr(session, make_repo(session))
    first = make_run(session, mr, head="deadbeef")
    first.status = ReviewRunStatus.COMPLETED
    session.flush()
    make_run(session, mr, head="deadbeef")
    session.flush()
    assert session.query(ReviewRunRow).count() == 2


def test_context_chunks_keep_their_order(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    for index in (0, 1):
        session.add(
            ContextPayloadRow(
                id=new_id(),
                review_run_id=run.id,
                chunk_index=index,
                tiers=["diff", "surrounding"],
                file_paths=["app/main.py"],
                token_count=100 + index,
                content_sha256=f"{index:064d}",
                body={"chunk": index},
            )
        )
    session.flush()
    stored = (
        session.query(ContextPayloadRow)
        .filter_by(review_run_id=run.id)
        .order_by(ContextPayloadRow.chunk_index)
        .all()
    )
    assert [c.chunk_index for c in stored] == [0, 1]
    assert stored[1].body == {"chunk": 1}


def test_a_chunk_index_cannot_repeat_within_a_run(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    for _ in range(2):
        session.add(
            ContextPayloadRow(
                id=new_id(),
                review_run_id=run.id,
                chunk_index=0,
                tiers=["diff"],
                file_paths=[],
                token_count=1,
                content_sha256="x" * 64,
                body={},
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def add_finding(
    session,
    run,
    *,
    path="app/main.py",
    line=10,
    side=DiffSide.NEW,
    category=FindingCategory.SECURITY,
):
    """Put the line number on whichever side the anchor names, as the domain does."""
    row = FindingRow(
        id=new_id(),
        review_run_id=run.id,
        file_path=path,
        side=side,
        old_line=line if side is DiffSide.OLD else None,
        new_line=line if side is DiffSide.NEW else None,
        category=category,
        severity=FindingSeverity.HIGH,
        message="m",
    )
    session.add(row)
    return row


def test_the_database_refuses_a_duplicate_finding(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    add_finding(session, run)
    add_finding(session, run)
    with pytest.raises(IntegrityError):
        session.flush()


def test_the_same_line_under_another_category_is_kept(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    add_finding(session, run, category=FindingCategory.SECURITY)
    add_finding(session, run, category=FindingCategory.PERFORMANCE)
    session.flush()
    assert session.query(FindingRow).count() == 2


def test_a_summary_comment_has_no_finding(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    session.add(
        PublishedCommentRow(
            id=new_id(),
            review_run_id=run.id,
            finding_id=None,
            provider_comment_id="c1",
            kind=CommentKind.SUMMARY,
            published_at=NOW,
        )
    )
    session.flush()
    assert session.query(PublishedCommentRow).one().finding_id is None


def test_a_finding_cannot_be_published_twice_in_one_run(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    finding = add_finding(session, run)
    session.flush()
    for n in (1, 2):
        session.add(
            PublishedCommentRow(
                id=new_id(),
                review_run_id=run.id,
                finding_id=finding.id,
                provider_comment_id=f"c{n}",
                kind=CommentKind.INLINE,
                published_at=NOW,
            )
        )
    with pytest.raises(IntegrityError):
        session.flush()


def test_generated_ids_sort_in_creation_order(session) -> None:
    ids = [new_id() for _ in range(20)]
    assert ids == sorted(ids, key=str)
    assert all(i.version == 7 for i in ids)


def test_the_database_refuses_a_duplicate_old_side_finding(session) -> None:
    """Old-side anchors carry new_line NULL, which a NULLS DISTINCT key ignores."""
    run = make_run(session, make_mr(session, make_repo(session)))
    add_finding(session, run, side=DiffSide.OLD, line=7)
    add_finding(session, run, side=DiffSide.OLD, line=7)
    with pytest.raises(IntegrityError):
        session.flush()


def test_two_old_side_findings_on_different_lines_are_kept(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    add_finding(session, run, side=DiffSide.OLD, line=7)
    add_finding(session, run, side=DiffSide.OLD, line=42)
    session.flush()
    assert session.query(FindingRow).count() == 2


def test_the_same_line_number_on_each_side_is_kept(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    add_finding(session, run, side=DiffSide.NEW, line=7)
    add_finding(session, run, side=DiffSide.OLD, line=7)
    session.flush()
    assert session.query(FindingRow).count() == 2


def add_summary(session, run, *, provider_comment_id):
    row = PublishedCommentRow(
        id=new_id(),
        review_run_id=run.id,
        finding_id=None,
        provider_comment_id=provider_comment_id,
        kind=CommentKind.SUMMARY,
        published_at=NOW,
    )
    session.add(row)
    return row


def test_a_summary_cannot_be_published_twice_in_one_run(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    add_summary(session, run, provider_comment_id="c1")
    add_summary(session, run, provider_comment_id="c2")
    with pytest.raises(IntegrityError):
        session.flush()


def test_each_run_keeps_its_own_summary(session) -> None:
    mr = make_mr(session, make_repo(session))
    first = make_run(session, mr, head="abc123", status=ReviewRunStatus.COMPLETED)
    second = make_run(session, mr, head="def456")
    add_summary(session, first, provider_comment_id="c1")
    add_summary(session, second, provider_comment_id="c2")
    session.flush()
    assert session.query(PublishedCommentRow).count() == 2
