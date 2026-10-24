"""Что схема отклоняет.

Каждый тест формулирует правило из спецификации модели данных и доказывает, что
его соблюдает база, а не надеется, что о нём вспомнит код приложения.
"""

import datetime as dt

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError, IntegrityError

from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    ReviewRunStage,
    ReviewRunStatus,
    ReviewRunStepStatus,
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
    ReviewRunStepRow,
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
        state=MergeRequestState.OPEN,
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


def test_a_merge_request_state_outside_the_enum_is_refused(session) -> None:
    """Сырой SQL, чтобы отказ пришёл от типа колонки, а не от ORM."""
    repo = make_repo(session)
    with pytest.raises(DataError):
        session.execute(
            text(
                "INSERT INTO merge_requests (id, repository_id, number, title, description,"
                " author, source_branch, target_branch, head_sha, state, created_at, updated_at)"
                " VALUES (:id, :repo, 1, 't', '', 'a', 'feat', 'main', 'abc123', 'draft',"
                " now(), now())"
            ),
            {"id": new_id(), "repo": repo.id},
        )


def delete_is_refused(session, row) -> None:
    """Удаление через statement, чтобы ORM не переупорядочила и не удалила заранее."""
    table = row.__table__
    with pytest.raises(IntegrityError, match="RestrictViolation"):
        session.execute(table.delete().where(table.c.id == row.id))


def test_deleting_a_repository_with_merge_requests_is_refused(session) -> None:
    repo = make_repo(session)
    make_mr(session, repo)
    delete_is_refused(session, repo)


def test_the_orm_cannot_delete_a_repository_out_from_under_its_children(session) -> None:
    """Когда дети загружены, ORM не должна ни удалять их, ни обнулять их ключ."""
    repo = make_repo(session)
    make_mr(session, repo)
    assert len(repo.merge_requests) == 1
    session.delete(repo)
    with pytest.raises(IntegrityError, match="RestrictViolation"):
        session.flush()


def test_deleting_a_merge_request_with_runs_is_refused(session) -> None:
    mr = make_mr(session, make_repo(session))
    make_run(session, mr)
    delete_is_refused(session, mr)


def test_deleting_a_run_with_a_context_payload_is_refused(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    session.add(
        ContextPayloadRow(
            id=new_id(),
            review_run_id=run.id,
            tiers=["diff"],
            file_paths=["app/main.py"],
            token_count=1,
            content_sha256="0" * 64,
            body={},
        )
    )
    session.flush()
    delete_is_refused(session, run)


def test_deleting_a_run_with_findings_is_refused(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    add_finding(session, run)
    session.flush()
    delete_is_refused(session, run)


def test_deleting_a_run_with_published_comments_is_refused(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    add_summary(session, run, provider_comment_id="c1")
    session.flush()
    delete_is_refused(session, run)


def test_deleting_a_published_finding_keeps_its_comment(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    finding = add_finding(session, run)
    session.flush()
    session.add(
        PublishedCommentRow(
            id=new_id(),
            review_run_id=run.id,
            finding_id=finding.id,
            provider_comment_id="c42",
            kind=CommentKind.INLINE,
            published_at=NOW,
        )
    )
    session.flush()
    delete_is_refused(session, finding)


def test_a_row_nothing_depends_on_can_be_deleted(session) -> None:
    repo = make_repo(session)
    session.delete(repo)
    session.flush()
    assert session.query(RepositoryRow).count() == 0


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
    """Номер строки ставим на ту сторону, которую называет привязка, как в домене."""
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


def test_a_provider_comment_id_cannot_be_recorded_twice(session) -> None:
    """Дубль id комментария у провайдера — ошибка, даже для разных замечаний и прогонов."""
    mr = make_mr(session, make_repo(session))
    first, second = make_run(session, mr), make_run(session, mr, head="def456")
    add_summary(session, first, provider_comment_id="c-dup")
    add_summary(session, second, provider_comment_id="c-dup")
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_summary_and_a_line_comment_may_share_a_provider_id(session) -> None:
    """У сводки id ревью, у комментария к строке - свой счётчик GitHub: совпадение не ошибка."""
    mr = make_mr(session, make_repo(session))
    first, second = make_run(session, mr), make_run(session, mr, head="def456")
    add_summary(session, first, provider_comment_id="c-same")
    add_summary(session, second, provider_comment_id="c-same", kind=CommentKind.THREAD_REPLY)
    session.flush()


def test_generated_ids_sort_in_creation_order(session) -> None:
    ids = [new_id() for _ in range(20)]
    assert ids == sorted(ids, key=str)
    assert all(i.version == 7 for i in ids)


def test_the_database_refuses_a_duplicate_old_side_finding(session) -> None:
    """У привязки на старой стороне new_line равен NULL, а ключ с NULLS DISTINCT
    такое игнорирует."""
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


def add_summary(session, run, *, provider_comment_id, kind=CommentKind.SUMMARY):
    row = PublishedCommentRow(
        id=new_id(),
        review_run_id=run.id,
        finding_id=None,
        provider_comment_id=provider_comment_id,
        kind=kind,
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


def test_a_failed_stage_on_a_run_that_did_not_fail_is_refused(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    run.status = ReviewRunStatus.ANALYSING
    run.failed_stage = ReviewRunStatus.ANALYSING
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_failed_stage_outside_the_processing_stages_is_refused(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    run.status = ReviewRunStatus.FAILED
    run.failed_stage = ReviewRunStatus.QUEUED
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_failed_run_stores_its_stage(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    run.status = ReviewRunStatus.FAILED
    run.failed_stage = ReviewRunStatus.PUBLISHING
    session.flush()
    session.refresh(run)
    assert run.failed_stage is ReviewRunStatus.PUBLISHING


def test_a_partial_rule_reference_is_refused(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    finding = add_finding(session, run)
    finding.rule_name = "Errors"
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_rule_check_below_one_is_refused(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    finding = add_finding(session, run)
    finding.rule_name = "Errors"
    finding.rule_check = 0
    finding.rule_check_text = "Отказ - значение Result"
    with pytest.raises(IntegrityError):
        session.flush()


def test_a_full_rule_reference_is_stored(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    finding = add_finding(session, run)
    finding.rule_name = "Errors"
    finding.rule_check = 1
    finding.rule_check_text = "Отказ - значение Result"
    session.flush()
    session.refresh(finding)
    assert (finding.rule_name, finding.rule_check) == ("Errors", 1)


def test_a_negative_verifier_drop_count_is_refused(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    run.verifier_dropped_findings = -1
    with pytest.raises(IntegrityError):
        session.flush()


def add_step(session, run, *, status=ReviewRunStepStatus.RUNNING, duration=None, error_code=None, tokens=None):
    row = ReviewRunStepRow(
        id=new_id(),
        review_run_id=run.id,
        stage=ReviewRunStage.ANALYSING,
        status=status,
        started_at=NOW,
        duration_seconds=duration,
        tokens_used=tokens,
        error_code=error_code,
    )
    session.add(row)
    return row


def test_deleting_a_run_with_steps_is_refused(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    add_step(session, run)
    session.flush()
    delete_is_refused(session, run)


def test_a_running_step_with_a_duration_is_refused(session) -> None:
    add_step(session, make_run(session, make_mr(session, make_repo(session))), duration=1.0)
    with pytest.raises(IntegrityError, match="ck_review_run_steps_duration"):
        session.flush()


def test_a_finished_step_without_a_duration_is_refused(session) -> None:
    add_step(
        session, make_run(session, make_mr(session, make_repo(session))), status=ReviewRunStepStatus.COMPLETED
    )
    with pytest.raises(IntegrityError, match="ck_review_run_steps_duration"):
        session.flush()


def test_a_failed_step_without_an_error_code_is_refused(session) -> None:
    add_step(
        session,
        make_run(session, make_mr(session, make_repo(session))),
        status=ReviewRunStepStatus.FAILED,
        duration=1.0,
    )
    with pytest.raises(IntegrityError, match="ck_review_run_steps_error_code"):
        session.flush()


def test_a_completed_step_with_an_error_code_is_refused(session) -> None:
    add_step(
        session,
        make_run(session, make_mr(session, make_repo(session))),
        status=ReviewRunStepStatus.COMPLETED,
        duration=1.0,
        error_code="LLM_TIMEOUT",
    )
    with pytest.raises(IntegrityError, match="ck_review_run_steps_error_code"):
        session.flush()


def test_negative_step_tokens_are_refused(session) -> None:
    add_step(
        session,
        make_run(session, make_mr(session, make_repo(session))),
        status=ReviewRunStepStatus.COMPLETED,
        duration=1.0,
        tokens=-1,
    )
    with pytest.raises(IntegrityError, match="ck_review_run_steps_tokens_non_negative"):
        session.flush()


def test_a_step_stage_outside_the_enum_is_refused(session) -> None:
    run = make_run(session, make_mr(session, make_repo(session)))
    with pytest.raises(DataError):
        session.execute(
            text(
                "INSERT INTO review_run_steps (id, review_run_id, stage, status, started_at)"
                " VALUES (:id, :run, 'thinking', 'running', now())"
            ),
            {"id": new_id(), "run": run.id},
        )
