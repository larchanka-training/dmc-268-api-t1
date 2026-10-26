"""Что адаптеры делают и что отклоняют."""

import datetime as dt
from dataclasses import replace

import pytest

from app.application.ports.unit_of_work import ActiveRunConflict
from app.application.stale_sweep import sweep_stale_runs
from app.domain.entities import (
    ContextPayload,
    DiffAnchor,
    Finding,
    MergeRequest,
    PublishedComment,
    Repository,
    ReviewRun,
    ReviewRunStep,
    RuleRef,
)
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
    VerificationOutcome,
)
from app.domain.ids import new_id
from app.domain.lifecycle import advance
from app.domain.run_steps import finish_step, start_step
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


def test_adding_a_taken_repository_reports_false_instead_of_raising(uow) -> None:
    """Гонка двух подключений: проигравший получает `False`, а не `IntegrityError`."""
    first = a_repository()
    twin = replace(a_repository(), full_name="acme/twin")
    with uow as work:
        assert work.repositories.add(first) is True
        work.commit()
    with uow as work:
        assert work.repositories.add(twin) is False
        work.commit()
    with uow as work:
        stored = work.repositories.find_by_provider(Provider.GITHUB, "1")
    assert stored == first


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


def test_a_payload_body_mutated_before_commit_is_stored_as_added(uow) -> None:
    """Мутация исходной сущности после `add` не меняет сохранённое."""
    with uow as work:
        _, _, run = seeded(work)
    payload = replace(
        a_context_payload(run.id, chunk_index=0, digest="d2"),
        body={"text": "x", "files": [{"path": "a.py"}]},
    )
    with uow as work:
        work.context_payloads.add(payload)
        payload.body["text"] = "mutated"
        payload.body["files"][0]["path"] = "mutated.py"
        work.commit()

    with uow as work:
        assert work.context_payloads.find_by_digest("d2").body == {
            "text": "x",
            "files": [{"path": "a.py"}],
        }


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


def a_step(run_id, stage: ReviewRunStage, started_at: dt.datetime) -> ReviewRunStep:
    return start_step(run_id, stage, started_at=started_at, step_id=new_id())


def test_steps_of_a_run_come_in_start_order_and_keep_their_outcome(uow) -> None:
    with uow as work:
        _, mr, run = seeded(work)
        other_run = replace(a_run(mr.id), head_sha="def456")
        work.review_runs.add(other_run)
        work.commit()
    # Вставка не в порядке начала: порядок даёт started_at, а не очередь записи.
    publishing = a_step(run.id, ReviewRunStage.PUBLISHING, LATER)
    fetching = a_step(run.id, ReviewRunStage.FETCHING_DIFF, NOW)
    first_try = a_step(run.id, ReviewRunStage.ANALYSING, NOW + dt.timedelta(minutes=1))
    retry = a_step(run.id, ReviewRunStage.ANALYSING, NOW + dt.timedelta(minutes=2))
    foreign = a_step(other_run.id, ReviewRunStage.FETCHING_DIFF, NOW)
    with uow as work:
        for step in (publishing, fetching, first_try, retry, foreign):
            work.review_run_steps.add(step)
        work.commit()
    with uow as work:
        work.review_run_steps.update(
            finish_step(first_try, duration_seconds=1.5, tokens_used=40, error_code="LLM_INVALID_OUTPUT")
        )
        work.review_run_steps.update(finish_step(retry, duration_seconds=2.0, tokens_used=60))
        work.commit()

    with uow as work:
        steps = work.review_run_steps.list_for_run(run.id)

    assert [s.id for s in steps] == [fetching.id, first_try.id, retry.id, publishing.id]
    failed, completed = steps[1], steps[2]
    assert failed == replace(
        first_try,
        status=ReviewRunStepStatus.FAILED,
        duration_seconds=1.5,
        tokens_used=40,
        error_code="LLM_INVALID_OUTPUT",
    )
    assert completed.status is ReviewRunStepStatus.COMPLETED
    assert (completed.duration_seconds, completed.tokens_used, completed.error_code) == (2.0, 60, None)
    assert steps[0].status is ReviewRunStepStatus.RUNNING
    assert steps[0].started_at == NOW
    assert steps[0].started_at.tzinfo is not None


def test_steps_with_one_start_are_ordered_by_id(uow) -> None:
    with uow as work:
        _, _, run = seeded(work)
    steps = [a_step(run.id, ReviewRunStage.ANALYSING, NOW) for _ in range(3)]
    with uow as work:
        for step in reversed(steps):
            work.review_run_steps.add(step)
        work.commit()
    with uow as work:
        assert [s.id for s in work.review_run_steps.list_for_run(run.id)] == [s.id for s in steps]


def test_a_run_without_steps_has_an_empty_list(uow) -> None:
    with uow as work:
        _, _, run = seeded(work)
    with uow as work:
        assert work.review_run_steps.list_for_run(run.id) == []


def test_updating_a_step_that_is_not_stored_is_an_error(uow) -> None:
    with uow as work:
        _, _, run = seeded(work)
    step = finish_step(a_step(run.id, ReviewRunStage.PUBLISHING, NOW), duration_seconds=1, tokens_used=None)
    with uow as work, pytest.raises(LookupError):
        work.review_run_steps.update(step)


def test_a_swept_run_has_no_running_steps_left(uow) -> None:
    with uow as work:
        _, _, run = seeded(work)
        work.review_runs.update(advance(run, ReviewRunStatus.BUILDING_CONTEXT, NOW).unwrap())
        work.commit()
    abandoned = a_step(run.id, ReviewRunStage.BUILDING_CONTEXT, NOW)
    with uow as work:
        work.review_run_steps.add(abandoned)
        work.commit()

    swept = sweep_stale_runs(
        uow,
        now=NOW + dt.timedelta(hours=1),
        limit=dt.timedelta(minutes=30),
        queued_limit=dt.timedelta(hours=6),
    )
    assert swept == 1

    with uow as work:
        assert work.review_runs.get(run.id).status is ReviewRunStatus.FAILED
        (step,) = work.review_run_steps.list_for_run(run.id)
    assert step == replace(
        abandoned,
        status=ReviewRunStepStatus.FAILED,
        duration_seconds=3600.0,
        tokens_used=None,
        error_code="REVIEW_RUN_STALE",
    )


def test_bot_threads_of_a_merge_request_keep_resolution_and_coverage(uow) -> None:
    """#148: треды бота на PR - открытые и закрытые, с отметкой бота; находка связывается с тредом."""
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        first, second = a_run(mr.id), replace(a_run(mr.id), head_sha="def456")
        work.review_runs.add(first)
        work.review_runs.add(second)
        work.commit()

    def finding(run_id, line):
        return Finding(
            id=new_id(),
            review_run_id=run_id,
            anchor=DiffAnchor("app/main.py", DiffSide.NEW, new_line=line),
            category=FindingCategory.SECURITY,
            severity=FindingSeverity.HIGH,
            message="m",
            created_at=NOW,
            updated_at=NOW,
        )

    open_finding, bot_closed_finding, repeated = finding(first.id, 1), finding(first.id, 2), finding(second.id, 1)

    def inline(finding_id, provider_comment_id, **flags):
        return PublishedComment(
            id=new_id(),
            review_run_id=first.id,
            finding_id=finding_id,
            provider_comment_id=provider_comment_id,
            kind=CommentKind.INLINE,
            published_at=NOW,
            created_at=NOW,
            updated_at=NOW,
            **flags,
        )

    open_root = inline(open_finding.id, "c1")
    closed_root = inline(bot_closed_finding.id, "c2", thread_resolved=True, thread_resolved_by_bot=True)
    with uow as work:
        for f in (open_finding, bot_closed_finding, repeated):
            work.findings.add(f)
        work.published_comments.add(open_root)
        work.published_comments.add(closed_root)
        work.commit()
    with uow as work:
        work.findings.mark_covered(repeated.id, open_finding.id)
        work.commit()

    with uow as work:
        threads = work.published_comments.list_threads_for_merge_request(mr.id)
        open_threads = work.published_comments.list_open_threads_for_merge_request(mr.id)
        (stored,) = work.findings.list_for_run(second.id)

    by_root = {t.root_provider_comment_id: (t.finding.id, t.resolved, t.resolved_by_bot) for t in threads}
    assert by_root == {
        "c1": (open_finding.id, False, False),
        "c2": (bot_closed_finding.id, True, True),
    }
    assert [t.root_provider_comment_id for t in open_threads] == ["c1"]
    assert stored.covered_by_finding_id == open_finding.id


def test_unpublished_failures_are_runs_that_died_publishing_without_a_comment(uow) -> None:
    """#222: кандидаты на подбор ревью - `failed` на `publishing` без `published_comments`,
    только своего PR."""

    def failed(mr_id, stage: ReviewRunStatus, at: dt.datetime) -> ReviewRun:
        return replace(
            a_run(mr_id), status=ReviewRunStatus.FAILED, failed_stage=stage, created_at=at
        )

    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr, other_mr = a_merge_request(repo.id), replace(a_merge_request(repo.id), number=2)
        work.merge_requests.add(mr)
        work.merge_requests.add(other_mr)
        orphan = failed(mr.id, ReviewRunStatus.PUBLISHING, LATER)
        earlier_orphan = failed(mr.id, ReviewRunStatus.PUBLISHING, NOW)
        recorded = failed(mr.id, ReviewRunStatus.PUBLISHING, NOW)
        analysing = failed(mr.id, ReviewRunStatus.ANALYSING, NOW)
        foreign = failed(other_mr.id, ReviewRunStatus.PUBLISHING, NOW)
        for run in (orphan, earlier_orphan, recorded, analysing, foreign):
            work.review_runs.add(run)
        work.commit()
    with uow as work:
        work.published_comments.add(
            PublishedComment(
                id=new_id(),
                review_run_id=recorded.id,
                finding_id=None,
                provider_comment_id="9001",
                kind=CommentKind.SUMMARY,
                published_at=NOW,
                created_at=NOW,
                updated_at=NOW,
            )
        )
        work.commit()

    with uow as work:
        found = work.review_runs.list_unpublished_failures(mr.id)

    assert [r.id for r in found] == [earlier_orphan.id, orphan.id]


def test_latest_run_on_a_head_is_the_newest_in_any_status(uow) -> None:
    """#222: за gate head отвечает последний прогон, в том числе упавший."""
    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        completed = replace(a_run(mr.id), status=ReviewRunStatus.COMPLETED, created_at=NOW)
        failed = replace(
            a_run(mr.id),
            status=ReviewRunStatus.FAILED,
            failed_stage=ReviewRunStatus.ANALYSING,
            created_at=LATER,
        )
        other_head = replace(a_run(mr.id), head_sha="other", created_at=LATER + dt.timedelta(minutes=1))
        for run in (completed, failed, other_head):
            work.review_runs.add(run)
        work.commit()

    with uow as work:
        latest = work.review_runs.find_latest(mr.id, "abc123")
        nothing = work.review_runs.find_latest(mr.id, "missing")

    assert latest is not None and latest.id == failed.id
    assert nothing is None
