"""Что адаптеры делают и что отклоняют."""

import datetime as dt
from dataclasses import replace

import pytest

from app.application.ports.unit_of_work import ActiveRunConflict
from app.domain.entities import (
    ContextPayload,
    DiffAnchor,
    Finding,
    MergeRequest,
    PublishedComment,
    Repository,
    ReviewRun,
    RuleRef,
)
from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    MergeRequestState,
    Provider,
    ReviewRunStatus,
    TriggerSource,
    VerificationOutcome,
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
        state=MergeRequestState.OPEN,
        created_at=NOW,
        updated_at=NOW,
    )


def a_run(mr_id) -> ReviewRun:
    return ReviewRun(
        id=new_id(),
        merge_request_id=mr_id,
        head_sha="abc123",
        base_sha="base789",
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
        # выход из блока без коммита обязан отбросить запись
    with uow as work:
        assert work.repositories.find_by_provider(Provider.GITHUB, "1") is None


def test_two_merge_requests_on_one_repo_and_number_map_to_active_run_conflict(
    uow,
) -> None:
    """`uq_merge_requests_repo_number` переводится в портовый ActiveRunConflict.

    Сопоставление идёт по тексту ошибки драйвера, поэтому проверяется на
    живом Postgres: на фейке оно ничего не доказывает.
    """
    repo = a_repository()
    with uow as work:
        work.repositories.add(repo)
        work.commit()
    with uow as work:
        work.merge_requests.add(a_merge_request(repo.id))
        work.commit()
    with uow as work, pytest.raises(ActiveRunConflict):
        work.merge_requests.add(a_merge_request(repo.id))
        work.commit()


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


def test_a_finished_run_refuses_a_same_status_update(uow) -> None:
    now = NOW
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run = a_run(mr.id)
        for status in (
            ReviewRunStatus.BUILDING_CONTEXT,
            ReviewRunStatus.ANALYSING,
            ReviewRunStatus.PUBLISHING,
        ):
            run = advance(run, status, now).unwrap()
        run = replace(
            advance(run, ReviewRunStatus.COMPLETED, now).unwrap(),
            model="m1",
            tokens_used=100,
            duration_seconds=1.5,
        )
        work.review_runs.add(run)
        work.commit()
    with uow as work:
        stored = work.review_runs.get(run.id)
        with pytest.raises(ValueError, match="terminal"):
            work.review_runs.update(
                replace(stored, model="m2", tokens_used=999, duration_seconds=9.9)
            )
    with uow as work:
        stored = work.review_runs.get(run.id)
        assert (stored.model, stored.tokens_used, stored.duration_seconds) == ("m1", 100, 1.5)


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
    """Репозиторий, запрос на изменения и прогон в очереди, всё закоммичено."""
    repo = a_repository()
    work.repositories.add(repo)
    mr = a_merge_request(repo.id)
    work.merge_requests.add(mr)
    run = a_run(mr.id)
    work.review_runs.add(run)
    work.commit()
    return repo, mr, run


def test_update_writes_the_counts_the_caller_carries(uow) -> None:
    """Счётчики отсева считает пайплайн и приносит в сущности; адаптер их пишет."""
    with uow as work:
        _, _, run = seeded(work)
    building = advance(run, ReviewRunStatus.BUILDING_CONTEXT, LATER).unwrap()
    with uow as work:
        work.review_runs.update(building)
        work.review_runs.update(replace(building, rejected_findings=2))
        work.commit()

    with uow as work:
        stored = work.review_runs.get(run.id)
    assert stored.rejected_findings == 2
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
    # Обе половины пары, по которой воспроизводится дифф, доживают до чтения.
    assert stored.head_sha == "abc123"
    assert stored.base_sha == "base789"


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
    """Несколько прогонов могут делить один digest; поиск не должен выбирать наугад."""
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


def test_a_failed_run_keeps_the_stage_it_failed_in(uow) -> None:
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run = a_run(mr.id)
        work.review_runs.add(run)
        work.commit()
    analysing = replace(run, status=ReviewRunStatus.BUILDING_CONTEXT)
    with uow as work:
        work.review_runs.update(analysing)
        work.commit()
    failed = replace(
        analysing,
        status=ReviewRunStatus.FAILED,
        failed_stage=ReviewRunStatus.BUILDING_CONTEXT,
        failure_reason="дифф недоступен",
        last_progress_at=LATER,
    )
    with uow as work:
        work.review_runs.update(failed)
        work.commit()
    with uow as work:
        stored = work.review_runs.get(run.id)
    assert stored.status is ReviewRunStatus.FAILED
    assert stored.failed_stage is ReviewRunStatus.BUILDING_CONTEXT


def _stored_run(uow) -> ReviewRun:
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run = a_run(mr.id)
        work.review_runs.add(run)
        work.commit()
    return run


def test_a_finding_keeps_its_rule_reference(uow) -> None:
    run = _stored_run(uow)
    finding = Finding(
        id=new_id(),
        review_run_id=run.id,
        anchor=DiffAnchor("app/main.py", DiffSide.NEW, new_line=10),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.MEDIUM,
        message="m",
        created_at=NOW,
        updated_at=NOW,
        rule=RuleRef(name="Errors", check=2, check_text="Отказ - значение Result"),
    )
    with uow as work:
        work.findings.add(finding)
        work.commit()
    with uow as work:
        (stored,) = work.findings.list_for_run(run.id)
    assert stored.rule == RuleRef(name="Errors", check=2, check_text="Отказ - значение Result")


def test_a_finding_without_a_rule_comes_back_without_one(uow) -> None:
    run = _stored_run(uow)
    finding = Finding(
        id=new_id(),
        review_run_id=run.id,
        anchor=DiffAnchor("app/main.py", DiffSide.NEW, new_line=10),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.MEDIUM,
        message="m",
        created_at=NOW,
        updated_at=NOW,
    )
    with uow as work:
        work.findings.add(finding)
        work.commit()
    with uow as work:
        (stored,) = work.findings.list_for_run(run.id)
    assert stored.rule is None


def test_a_run_keeps_its_verification_outcome(uow) -> None:
    run = _stored_run(uow)
    with uow as work:
        assert work.review_runs.get(run.id).verification is None
    building = replace(run, status=ReviewRunStatus.BUILDING_CONTEXT)
    analysing = replace(
        building,
        status=ReviewRunStatus.ANALYSING,
        verification=VerificationOutcome.VERIFIED,
        verifier_dropped_findings=2,
        last_progress_at=LATER,
    )
    with uow as work:
        work.review_runs.update(building)
        work.review_runs.update(analysing)
        work.commit()
    with uow as work:
        stored = work.review_runs.get(run.id)
    assert stored.verification is VerificationOutcome.VERIFIED
    assert stored.verifier_dropped_findings == 2


def test_a_run_is_inserted_with_its_verification_outcome(uow) -> None:
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run = replace(
            a_run(mr.id),
            verification=VerificationOutcome.UNAVAILABLE,
            verifier_dropped_findings=1,
        )
        work.review_runs.add(run)
        work.commit()
    with uow as work:
        stored = work.review_runs.get(run.id)
    assert stored.verification is VerificationOutcome.UNAVAILABLE
    assert stored.verifier_dropped_findings == 1


def test_published_comments_of_a_run_come_in_publication_order(uow) -> None:
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run, other_run = a_run(mr.id), replace(a_run(mr.id), head_sha="def456")
        work.review_runs.add(run)
        work.review_runs.add(other_run)
        work.commit()
    findings = [
        Finding(
            id=new_id(),
            review_run_id=run.id,
            anchor=DiffAnchor("app/main.py", DiffSide.NEW, new_line=line),
            category=FindingCategory.SECURITY,
            severity=FindingSeverity.HIGH,
            message="m",
            created_at=NOW,
            updated_at=NOW,
        )
        for line in (1, 2, 3)
    ]

    def comment(review_run_id, finding_id, kind, published_at, provider_comment_id):
        return PublishedComment(
            id=new_id(),
            review_run_id=review_run_id,
            finding_id=finding_id,
            provider_comment_id=provider_comment_id,
            kind=kind,
            published_at=published_at,
            created_at=published_at,
            updated_at=published_at,
        )

    # Вставка — не в порядке публикации; у двух инлайнов одно время, их разводит id.
    later = comment(run.id, findings[2].id, CommentKind.INLINE, LATER, "c3")
    same_a = comment(run.id, findings[0].id, CommentKind.THREAD_REPLY, NOW, "c1")
    same_b = comment(run.id, findings[1].id, CommentKind.INLINE, NOW, "c2")
    foreign = comment(other_run.id, None, CommentKind.SUMMARY, NOW, "c4")
    with uow as work:
        for finding in findings:
            work.findings.add(finding)
        for c in (later, same_a, same_b, foreign):
            work.published_comments.add(c)
        work.commit()

    with uow as work:
        stored = work.published_comments.list_for_run(run.id)

    same_time = sorted((same_a, same_b), key=lambda c: c.id)
    assert [c.id for c in stored] == [*(c.id for c in same_time), later.id]
