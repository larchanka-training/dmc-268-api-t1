"""Что адаптеры делают и что отклоняют."""

import datetime as dt
import threading
import time
from dataclasses import replace
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.application.ports.unit_of_work import ActiveRunConflict, RunStateConflict
from app.application.stale_sweep import sweep_stale_runs
from app.domain.entities import (
    BalanceCredit,
    ContextPayload,
    DiffAnchor,
    Finding,
    MergeRequest,
    PublishedComment,
    Repository,
    RepositorySettings,
    ReviewRun,
    ReviewRunStep,
    RuleRef,
    User,
)
from app.domain.enums import (
    CommentKind,
    CoverageReason,
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
from app.domain.llm_usage import NO_USAGE, LlmUsage
from app.domain.payload_retention import BodyExpiryStats
from app.domain.run_steps import finish_step, start_step
from app.domain.secret_redaction import REDACTED
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
        connected_at=NOW,
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


def test_disconnect_and_reconnect_keep_one_record_with_its_id(uow) -> None:
    """Мягкое выключение: запись та же, `connected_at` пуст; включение возвращает прежний `id`."""
    repo = a_repository()
    with uow as work:
        work.repositories.add(repo)
        work.commit()
    with uow as work:
        work.repositories.disconnect(repo.id, LATER)
        work.repositories.disconnect(repo.id, LATER)
        work.commit()
    with uow as work:
        disconnected = work.repositories.get(repo.id)
    assert disconnected == replace(repo, connected_at=None, updated_at=LATER)

    later = LATER + dt.timedelta(minutes=1)
    candidate = replace(a_repository(), full_name="acme/renamed", connected_at=later, updated_at=later)
    with uow as work:
        reconnected = work.repositories.reconnect(candidate)
        # Уже включён — второе включение ничего не находит.
        again = work.repositories.reconnect(candidate)
        work.commit()
    assert reconnected == replace(repo, full_name="acme/renamed", connected_at=later, updated_at=later)
    assert again is None
    with uow as work:
        assert work.repositories.get(repo.id) == reconnected


def test_reconnect_without_a_record_finds_nothing(uow) -> None:
    with uow as work:
        assert work.repositories.reconnect(a_repository()) is None


HIGH_ON_MAIN = RepositorySettings(
    auto_review_enabled=False,
    branch_filter=("main", "release/*"),
    min_severity=FindingSeverity.HIGH,
)


def test_a_repository_is_added_and_read_with_its_settings(uow) -> None:
    """`text[]` ↔ tuple и enum порога переживают запись и чтение."""
    repo = replace(a_repository(), branch_filter=("main", "release/*"), min_severity=FindingSeverity.HIGH)
    with uow as work:
        work.repositories.add(repo)
        work.commit()
    with uow as work:
        assert work.repositories.get(repo.id) == repo


def test_update_settings_writes_three_columns_and_updated_at(uow) -> None:
    repo = a_repository()
    with uow as work:
        work.repositories.add(repo)
        work.commit()
    with uow as work:
        returned = work.repositories.update_settings(repo.id, HIGH_ON_MAIN, LATER)
        work.commit()
    with uow as work:
        stored = work.repositories.get(repo.id)
    assert returned == stored
    assert stored == replace(
        repo,
        auto_review_enabled=False,
        branch_filter=("main", "release/*"),
        min_severity=FindingSeverity.HIGH,
        updated_at=LATER,
    )

    # Полная замена: пустой список стирает паттерны.
    with uow as work:
        work.repositories.update_settings(
            repo.id,
            RepositorySettings(
                auto_review_enabled=True, branch_filter=(), min_severity=FindingSeverity.CRITICAL
            ),
            LATER,
        )
        work.commit()
    with uow as work:
        assert work.repositories.get(repo.id) == replace(repo, updated_at=LATER)


def test_update_settings_of_a_missing_record_returns_none(uow) -> None:
    with uow as work:
        assert work.repositories.update_settings(a_repository().id, HIGH_ON_MAIN, LATER) is None
        work.commit()


def test_disconnect_and_reconnect_keep_the_saved_settings(uow) -> None:
    repo = a_repository()
    with uow as work:
        work.repositories.add(repo)
        work.repositories.update_settings(repo.id, HIGH_ON_MAIN, NOW)
        work.repositories.disconnect(repo.id, LATER)
        work.commit()
    # Путь подключения несёт умолчания в сущности — они не должны затереть сохранённое.
    candidate = replace(a_repository(), connected_at=LATER, updated_at=LATER)
    with uow as work:
        reconnected = work.repositories.reconnect(candidate)
        work.commit()
    assert reconnected is not None
    assert (reconnected.auto_review_enabled, reconnected.branch_filter, reconnected.min_severity) == (
        False,
        ("main", "release/*"),
        FindingSeverity.HIGH,
    )


def test_two_concurrent_reconnects_leave_one_record_and_one_winner(clean_db) -> None:
    """Гонка включений выключенного: второй UPDATE ждёт строку, перечитывает её и не совпадает,
    вставка упирается в ключ — проигравший получает «уже подключён», а не ошибку и не дубль."""
    repo = replace(a_repository(), connected_at=None)
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.repositories.add(repo)
        work.commit()
    first = replace(a_repository(), connected_at=LATER, updated_at=LATER)
    second = replace(a_repository(), full_name="acme/second", connected_at=LATER, updated_at=LATER)
    locked = threading.Event()
    results: list[tuple[Repository | None, bool]] = []

    def late_connect() -> None:
        locked.wait()
        with SqlAlchemyUnitOfWork(clean_db) as work:
            # Те же два шага, что у `connect_repository`.
            reconnected = work.repositories.reconnect(second)
            results.append((reconnected, reconnected is None and work.repositories.add(second)))
            work.commit()

    thread = threading.Thread(target=late_connect)
    thread.start()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        assert work.repositories.reconnect(first) is not None
        locked.set()
        time.sleep(0.3)
        work.commit()
    thread.join(timeout=10)

    assert results == [(None, False)]
    with clean_db.connect() as conn:
        rows = conn.execute(text("SELECT id, full_name, connected_at FROM repositories")).all()
    assert [tuple(row) for row in rows] == [(repo.id, "acme/api", LATER)]


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


def test_a_cancel_committed_during_a_worker_transition_is_not_overwritten(clean_db) -> None:
    """Ревью #225: воркер прочитал прогон `analysing`, приём в это время коммитит
    `cancelled` (смена целевой ветки). Переход воркера не воскрешает прогон: иначе
    вставка нового прогона упрётся в индекс активного, а старый опубликует `success`
    по диффу против прежней ветки."""
    with SqlAlchemyUnitOfWork(clean_db) as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run = a_run(mr.id)
        for status in (ReviewRunStatus.BUILDING_CONTEXT, ReviewRunStatus.ANALYSING):
            run = advance(run, status, NOW).unwrap()
        work.review_runs.add(run)
        work.commit()
    read = threading.Event()

    def cancel() -> None:
        read.wait()
        with SqlAlchemyUnitOfWork(clean_db) as work:
            work.review_runs.update(advance(run, ReviewRunStatus.CANCELLED, LATER).unwrap())
            work.commit()

    thread = threading.Thread(target=cancel)
    thread.start()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.review_runs.update(advance(run, ReviewRunStatus.PUBLISHING, LATER).unwrap())
        read.set()
        time.sleep(0.3)
        work.commit()
    thread.join(timeout=10)

    with SqlAlchemyUnitOfWork(clean_db) as work:
        stored = work.review_runs.get(run.id)
    assert stored is not None and stored.status is ReviewRunStatus.CANCELLED


def _run_in_db(clean_db, status: ReviewRunStatus = ReviewRunStatus.QUEUED) -> ReviewRun:
    with SqlAlchemyUnitOfWork(clean_db) as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr = a_merge_request(repo.id)
        work.merge_requests.add(mr)
        run = a_run(mr.id)
        if status is not ReviewRunStatus.QUEUED:
            run = advance(run, ReviewRunStatus.BUILDING_CONTEXT, NOW).unwrap()
        work.review_runs.add(run)
        work.commit()
    return run


def _write(clean_db, run: ReviewRun, previous: ReviewRun | None = None) -> None:
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.review_runs.update(run, previous=previous)
        work.commit()


def test_two_workers_claiming_one_run_leave_one_winner(clean_db) -> None:
    """Оба прочитали `queued`. Смена статуса тут ни при чём: второй пишет тот же статус."""
    run = _run_in_db(clean_db)
    claimed = advance(run, ReviewRunStatus.BUILDING_CONTEXT, LATER).unwrap()

    _write(clean_db, claimed, previous=run)
    with pytest.raises(RunStateConflict):
        _write(clean_db, claimed, previous=run)


def test_a_sweep_does_not_fail_a_run_that_progressed_after_its_read(clean_db) -> None:
    run = _run_in_db(clean_db, ReviewRunStatus.BUILDING_CONTEXT)
    _write(clean_db, replace(run, last_progress_at=LATER))

    with pytest.raises(RunStateConflict):
        _write(clean_db, advance(run, ReviewRunStatus.FAILED, LATER).unwrap(), previous=run)
    with SqlAlchemyUnitOfWork(clean_db) as work:
        stored = work.review_runs.get(run.id)
    assert stored is not None and stored.status is ReviewRunStatus.BUILDING_CONTEXT


def test_two_concurrent_sweeps_fail_a_run_once(clean_db) -> None:
    """Вторая очистка ждёт блокировку первой и после её коммита видит чужую запись."""
    run = _run_in_db(clean_db, ReviewRunStatus.BUILDING_CONTEXT)
    failed = advance(run, ReviewRunStatus.FAILED, LATER).unwrap()
    errors: list[Exception] = []
    locked = threading.Event()

    def second_sweep() -> None:
        assert locked.wait(timeout=10)
        try:
            _write(clean_db, failed, previous=run)
        except RunStateConflict as error:
            errors.append(error)

    thread = threading.Thread(target=second_sweep, daemon=True)
    thread.start()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.review_runs.update(failed, previous=run)
        locked.set()
        time.sleep(0.3)
        work.commit()
    thread.join(timeout=10)

    assert len(errors) == 1


def test_updated_at_is_the_write_time_not_the_transaction_start(clean_db) -> None:
    """Запись после ожидания блокировки не получает метку раньше коммита соседа."""
    run = _run_in_db(clean_db, ReviewRunStatus.BUILDING_CONTEXT)
    with SqlAlchemyUnitOfWork(clean_db) as work:
        started = work.session.scalar(text("SELECT now()"))
        time.sleep(0.05)
        work.review_runs.update(replace(run, last_progress_at=LATER))
        work.commit()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        stored = work.review_runs.get(run.id)
    assert stored is not None and stored.updated_at >= started + dt.timedelta(seconds=0.05)


def test_a_step_outcome_is_written_once(clean_db) -> None:
    """Воркер и очистка закрывают один шаг: первый исход остаётся."""
    run = _run_in_db(clean_db, ReviewRunStatus.BUILDING_CONTEXT)
    step = start_step(run.id, ReviewRunStage.BUILDING_CONTEXT, started_at=NOW, step_id=new_id())
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.review_run_steps.add(step)
        work.commit()
    first = finish_step(step, duration_seconds=1.0, tokens_used=None)
    second = finish_step(step, duration_seconds=9.0, tokens_used=None, error_code="REVIEW_RUN_STALE")

    for outcome in (first, second):
        with SqlAlchemyUnitOfWork(clean_db) as work:
            work.review_run_steps.update(outcome)
            work.commit()

    with SqlAlchemyUnitOfWork(clean_db) as work:
        (stored,) = work.review_run_steps.list_for_run(run.id)
    assert stored.status is ReviewRunStepStatus.COMPLETED
    assert stored.error_code is None


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


def test_a_run_reads_back_its_usage_breakdown_and_cost(uow) -> None:
    # Стоимость - со всеми 10 знаками колонки: из базы она должна прийти той же.
    usage = LlmUsage(
        input_tokens=2100,
        output_tokens=350,
        cached_tokens=500,
        reasoning_tokens=40,
        total_tokens=2450,
        cost_eur=Decimal("13.0960038381"),
    )
    with uow as work:
        _, _, run = seeded(work)
    with uow as work:
        assert work.review_runs.get(run.id).usage is None
    building = advance(run, ReviewRunStatus.BUILDING_CONTEXT, LATER).unwrap()
    with uow as work:
        work.review_runs.update(replace(building, tokens_used=usage.total_tokens, usage=usage))
        work.commit()

    with uow as work:
        stored = work.review_runs.get(run.id)
    assert (stored.usage, stored.tokens_used) == (usage, 2450)
    assert str(stored.usage.cost_eur) == "13.0960038381"


def test_tokens_used_is_written_from_usage_so_the_two_cannot_drift(uow) -> None:
    usage = LlmUsage(input_tokens=30, output_tokens=5, total_tokens=35, cost_eur=Decimal("0.0000913"))
    with uow as work:
        _, _, run = seeded(work)
    building = advance(run, ReviewRunStatus.BUILDING_CONTEXT, LATER).unwrap()
    with uow as work:
        # `replace` без `tokens_used`: в базе итог всё равно из usage.
        work.review_runs.update(replace(building, usage=usage))
        work.commit()

    with uow as work:
        stored = work.review_runs.get(run.id)
    assert (stored.tokens_used, stored.usage.total_tokens) == (35, 35)


@pytest.mark.parametrize(
    "columns",
    [
        "cost_eur = 0.5",
        "input_tokens = 1, output_tokens = 1, cached_tokens = 0, reasoning_tokens = 0, cost_eur = 0, tokens_used = NULL",
        "input_tokens = -1, output_tokens = 1, cached_tokens = 0, reasoning_tokens = 0, cost_eur = 0, tokens_used = 1",
        "input_tokens = 1, output_tokens = 1, cached_tokens = 0, reasoning_tokens = 0, cost_eur = -0.1, tokens_used = 1",
    ],
    ids=["cost-without-tokens", "usage-without-total", "negative-tokens", "negative-cost"],
)
def test_usage_columns_are_written_whole_and_non_negative(uow, columns: str) -> None:
    """CHECK держит инвариант маппера против любого писателя: скрипта, psql, бэкфилла."""
    with uow as work:
        _, _, run = seeded(work)
    with uow as work, pytest.raises(IntegrityError, match="ck_review_runs_usage"):
        work.session.execute(text(f"UPDATE review_runs SET {columns} WHERE id = :id"), {"id": run.id})
        work.session.flush()


def test_a_cancelled_run_takes_its_spend_and_keeps_its_status(uow) -> None:
    usage = LlmUsage(input_tokens=900, output_tokens=100, total_tokens=1000, cost_eur=Decimal("0.0000913"))
    with uow as work:
        _, _, run = seeded(work)
    cancelled = advance(run, ReviewRunStatus.CANCELLED, LATER).unwrap()
    with uow as work:
        work.review_runs.update(cancelled)
        work.commit()

    with uow as work:
        work.review_runs.record_cancelled_spend(run.id, usage)
        work.commit()

    with uow as work:
        stored = work.review_runs.get(run.id)
    assert stored.status is ReviewRunStatus.CANCELLED
    assert (stored.usage, stored.tokens_used) == (usage, 1000)


def test_spend_is_not_recorded_on_a_run_that_is_not_cancelled(uow) -> None:
    with uow as work:
        _, _, run = seeded(work)
        work.review_runs.record_cancelled_spend(run.id, LlmUsage(input_tokens=1, total_tokens=1))
        work.commit()

    with uow as work:
        stored = work.review_runs.get(run.id)
    assert (stored.usage, stored.tokens_used) == (None, None)


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


def test_a_stored_payload_has_secrets_redacted(uow) -> None:
    """#28: в базу пакет идёт без секретов, остальной текст и структура — как есть."""
    with uow as work:
        _, _, run = seeded(work)
    token = "ghp_" + "a" * 36
    payload = replace(
        a_context_payload(run.id, chunk_index=0, digest="d0"),
        body={"diff": [{"path": "a.py", "hunk": f'+TOKEN = "{token}"\n+x = 1'}], "omitted_count": 0},
    )
    with uow as work:
        work.context_payloads.add(payload)
        work.commit()

    with uow as work:
        (stored,) = work.context_payloads.list_for_run(run.id)
    assert stored.body == {
        "diff": [{"path": "a.py", "hunk": f'+TOKEN = "{REDACTED}"\n+x = 1'}],
        "omitted_count": 0,
    }
    assert token in payload.body["diff"][0]["hunk"]  # сущность, которая уходит модели, не тронута


def test_a_payload_body_is_a_copy_not_a_handle_on_the_row(uow) -> None:
    with uow as work:
        _, _, run = seeded(work)
    payload = a_context_payload(run.id, chunk_index=0, digest="d1")
    with uow as work:
        work.context_payloads.add(payload)
        work.commit()

    with uow as work:
        (loaded,) = work.context_payloads.list_for_run(run.id)
        loaded.body["text"] = "mutated"
        work.commit()

    with uow as work:
        assert work.context_payloads.list_for_run(run.id)[0].body == {"text": "x"}


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
        assert work.context_payloads.list_for_run(payload.review_run_id)[0].body == {
            "text": "x",
            "files": [{"path": "a.py"}],
        }


def _payloads_aged(uow, run_id, ages: list[dt.timedelta]) -> list[ContextPayload]:
    """Пакеты прогона с `created_at` = сейчас минус возраст: время записи ставит база."""
    payloads = [a_context_payload(run_id, chunk_index=i, digest=f"age{i}") for i in range(len(ages))]
    with uow as work:
        for payload in payloads:
            work.context_payloads.add(payload)
        work.commit()
    with uow as work:
        for payload, age in zip(payloads, ages, strict=True):
            work.session.execute(
                text("UPDATE context_payloads SET created_at = now() - :age WHERE id = :id"),
                {"age": age, "id": payload.id},
            )
        work.commit()
    return payloads


def test_expire_bodies_clears_old_bodies_and_keeps_metadata(uow) -> None:
    """Change context-payload-retention: тело старше срока стирается, метаданные остаются."""
    with uow as work:
        _, _, run = seeded(work)
    _payloads_aged(uow, run.id, [dt.timedelta(days=40), dt.timedelta(days=1)])
    now = dt.datetime.now(dt.UTC)

    with uow as work:
        expired = work.context_payloads.expire_bodies(now - dt.timedelta(days=30), now, 10)
        work.commit()

    assert expired == 1
    with uow as work:
        old, fresh = work.context_payloads.list_for_run(run.id)
    assert (old.body, old.body_expired_at) == (None, now)
    assert (old.file_paths, old.token_count, old.content_sha256) == (("app/main.py",), 10, "age0")
    assert (fresh.body, fresh.body_expired_at) == ({"text": "x"}, None)


def test_expire_bodies_takes_the_oldest_first_and_stops_at_the_limit(uow) -> None:
    with uow as work:
        _, _, run = seeded(work)
    payloads = _payloads_aged(
        uow, run.id, [dt.timedelta(days=31), dt.timedelta(days=60), dt.timedelta(days=45)]
    )
    now = dt.datetime.now(dt.UTC)
    cutoff = now - dt.timedelta(days=30)

    with uow as work:
        assert work.context_payloads.expire_bodies(cutoff, now, 2) == 2
        work.commit()
    with uow as work:
        expired = {p.id for p in work.context_payloads.list_for_run(run.id) if p.body_expired_at}
    assert expired == {payloads[1].id, payloads[2].id}

    # Стёртое второй раз не трогается: проход берёт только живые тела.
    with uow as work:
        assert work.context_payloads.expire_bodies(cutoff, now, 10) == 1
        assert work.context_payloads.expire_bodies(cutoff, now, 10) == 0
        work.commit()


def test_body_expiry_stats_counts_erased_bodies_and_takes_the_last_expiry(uow) -> None:
    """Change diff-retention-visibility: агрегат по прогону, стёрты все тела."""
    with uow as work:
        _, _, run = seeded(work)
    _payloads_aged(uow, run.id, [dt.timedelta(days=60), dt.timedelta(days=40)])
    first = dt.datetime(2026, 11, 1, 3, 0, tzinfo=dt.UTC)
    last = dt.datetime(2026, 11, 2, 3, 0, tzinfo=dt.UTC)
    cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=30)
    with uow as work:
        assert work.context_payloads.expire_bodies(cutoff, first, 1) == 1
        assert work.context_payloads.expire_bodies(cutoff, last, 1) == 1
        work.commit()

    with uow as work:
        stats = work.context_payloads.body_expiry_stats_for_run(run.id)

    assert stats == BodyExpiryStats(payload_count=2, erased_count=2, last_expired_at=last)


def test_body_expiry_stats_counts_live_bodies(uow) -> None:
    with uow as work:
        _, _, run = seeded(work)
    _payloads_aged(uow, run.id, [dt.timedelta(days=40), dt.timedelta(days=1)])
    now = dt.datetime(2026, 11, 2, 3, 0, tzinfo=dt.UTC)
    with uow as work:
        work.context_payloads.expire_bodies(dt.datetime.now(dt.UTC) - dt.timedelta(days=30), now, 10)
        work.commit()

    with uow as work:
        stats = work.context_payloads.body_expiry_stats_for_run(run.id)

    assert stats == BodyExpiryStats(payload_count=2, erased_count=1, last_expired_at=now)


def test_body_expiry_stats_of_a_run_without_payloads_is_empty(uow) -> None:
    with uow as work:
        _, _, run = seeded(work)
        stats = work.context_payloads.body_expiry_stats_for_run(run.id)

    assert stats == BodyExpiryStats(payload_count=0, erased_count=0, last_expired_at=None)


def test_a_payload_cannot_lose_its_body_without_the_expiry_mark(uow) -> None:
    """Check-ограничение: тела нет ровно тогда, когда отмечено время стирания."""
    with uow as work:
        _, _, run = seeded(work)
    (payload,) = _payloads_aged(uow, run.id, [dt.timedelta(days=1)])
    with pytest.raises(Exception, match="ck_context_payloads_body_expiry"), uow as work:
        work.session.execute(
            text("UPDATE context_payloads SET body = NULL WHERE id = :id"), {"id": payload.id}
        )
        work.commit()


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
    только своего PR и упавшие позже последней записи: упавшие раньше уже проверяли.
    Время падения - `last_progress_at`, а не создание (ревью #225)."""

    def failed(
        mr_id, stage: ReviewRunStatus, at: dt.datetime, created_at: dt.datetime | None = None
    ) -> ReviewRun:
        return replace(
            a_run(mr_id),
            status=ReviewRunStatus.FAILED,
            failed_stage=stage,
            created_at=created_at or at,
            last_progress_at=at,
        )

    with uow as work:
        repo = a_repository()
        work.repositories.add(repo)
        mr, other_mr = a_merge_request(repo.id), replace(a_merge_request(repo.id), number=2)
        work.merge_requests.add(mr)
        work.merge_requests.add(other_mr)
        orphan = failed(mr.id, ReviewRunStatus.PUBLISHING, LATER)
        earlier_orphan = failed(mr.id, ReviewRunStatus.PUBLISHING, NOW)
        recorded = failed(mr.id, ReviewRunStatus.PUBLISHING, NOW - dt.timedelta(minutes=1))
        checked_before = failed(mr.id, ReviewRunStatus.PUBLISHING, NOW - dt.timedelta(minutes=2))
        # Создан раньше записанного, а упал (выметатель) уже после его публикации.
        swept_later = failed(
            mr.id,
            ReviewRunStatus.PUBLISHING,
            NOW + dt.timedelta(minutes=1),
            created_at=NOW - dt.timedelta(minutes=3),
        )
        analysing = failed(mr.id, ReviewRunStatus.ANALYSING, NOW)
        foreign = failed(other_mr.id, ReviewRunStatus.PUBLISHING, NOW)
        for run in (orphan, earlier_orphan, recorded, checked_before, swept_later, analysing, foreign):
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
                published_at=NOW - dt.timedelta(minutes=1),
                created_at=NOW,
                updated_at=NOW,
            )
        )
        work.commit()

    with uow as work:
        found = work.review_runs.list_unpublished_failures(mr.id)

    assert [r.id for r in found] == [swept_later.id, earlier_orphan.id, orphan.id]


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


# --- биллинг (`review-billing`) ---------------------------------------------------


def a_user(login: str = "alice") -> User:
    return User(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_user_id=login,
        login=login,
        name=None,
        avatar_url=None,
        created_at=NOW,
        updated_at=NOW,
    )


def a_credit(user_id, amount: str) -> BalanceCredit:
    return BalanceCredit(
        id=new_id(), user_id=user_id, amount_eur=Decimal(amount), note="тест", credited_by="admin", created_at=NOW
    )


def spent(cost: str, tokens: int = 1000) -> LlmUsage:
    return LlmUsage(input_tokens=tokens, total_tokens=tokens, cost_eur=Decimal(cost))


def billed_runs(work, *runs: ReviewRun) -> None:
    """Прогоны на одном запросе на изменения: репозиторий и PR — свои на каждый вызов."""
    repo = replace(a_repository(), id=new_id(), provider_id=str(new_id().int))
    work.repositories.add(repo)
    mr = a_merge_request(repo.id)
    work.merge_requests.add(mr)
    for run in runs:
        work.review_runs.add(replace(run, merge_request_id=mr.id))


def finished(**fields) -> ReviewRun:
    return replace(a_run(None), status=ReviewRunStatus.COMPLETED, **fields)


def test_balance_is_credits_minus_cost_or_reserve_of_paid_runs(uow) -> None:
    alice, bob = a_user("alice"), a_user("bob")
    with uow as work:
        work.users.add(alice)
        work.users.add(bob)
        work.billing.add_credit(a_credit(alice.id, "5"))
        work.billing.add_credit(a_credit(alice.id, "-0.5"))
        billed_runs(
            work,
            # Факт важнее резерва.
            finished(payer_user_id=alice.id, estimated_cost_eur=Decimal("0.40"), usage=spent("0.30")),
            # Резерв, пока факта нет.
            replace(a_run(None), status=ReviewRunStatus.BUILDING_CONTEXT, payer_user_id=alice.id,
                    estimated_cost_eur=Decimal("0.20")),
            # Прогон без денег: нулевой расход.
            finished(payer_user_id=alice.id, usage=NO_USAGE, coverage_reason=CoverageReason.BALANCE),
            # Чужие и без плательщика не в счёт.
            finished(payer_user_id=bob.id, usage=spent("1")),
            finished(usage=spent("1")),
        )
        work.commit()

    with uow as work:
        assert work.billing.balance(alice.id) == Decimal("4.00")
        assert work.billing.balance(bob.id) == Decimal(-1)
        assert work.billing.balance(new_id()) == Decimal(0)


def test_a_run_keeps_its_payer_reserve_and_coverage_reason(uow) -> None:
    alice = a_user()
    run = finished(payer_user_id=alice.id, estimated_cost_eur=Decimal("0.0000913"),
                   coverage_reason=CoverageReason.BALANCE)
    with uow as work:
        work.users.add(alice)
        billed_runs(work, run)
        work.commit()
    with uow as work:
        stored = work.review_runs.get(run.id)
    assert (stored.payer_user_id, stored.estimated_cost_eur, stored.coverage_reason) == (
        alice.id, Decimal("0.0000913"), CoverageReason.BALANCE
    )


def test_a_stale_copy_does_not_erase_the_reserve(uow) -> None:
    """Отмена (`supersede`) по копии, прочитанной до резерва, резерв не стирает."""
    alice = a_user()
    queued = replace(a_run(None), status=ReviewRunStatus.BUILDING_CONTEXT)
    with uow as work:
        work.users.add(alice)
        billed_runs(work, queued)
        work.commit()
    with uow as work:
        stale = work.review_runs.get(queued.id)
        work.review_runs.update(replace(stale, payer_user_id=alice.id, estimated_cost_eur=Decimal("0.05")))
        work.commit()
    with uow as work:
        work.review_runs.update(advance(stale, ReviewRunStatus.CANCELLED, LATER).unwrap())
        work.commit()
    with uow as work:
        stored = work.review_runs.get(queued.id)
    assert (stored.status, stored.payer_user_id, stored.estimated_cost_eur) == (
        ReviewRunStatus.CANCELLED, alice.id, Decimal("0.05")
    )


def test_cancelled_spend_can_be_zero(uow) -> None:
    """Резерв отменённого до `analysing` прогона закрывается нулевым расходом (design D2)."""
    alice = a_user()
    run = replace(a_run(None), status=ReviewRunStatus.CANCELLED, payer_user_id=alice.id,
                  estimated_cost_eur=Decimal("0.05"))
    with uow as work:
        work.users.add(alice)
        work.billing.add_credit(a_credit(alice.id, "1"))
        billed_runs(work, run)
        work.commit()
    with uow as work:
        work.review_runs.record_cancelled_spend(run.id, NO_USAGE)
        work.commit()
    with uow as work:
        assert work.billing.balance(alice.id) == Decimal(1)


def test_price_is_taken_from_the_last_fifty_paid_runs_of_any_model(uow) -> None:
    """В `model` — id апстрима роутера (`tensorix/glm-5.3`), поэтому модель не фильтруется (D4)."""
    with uow as work:
        assert work.billing.eur_per_token() == Decimal(0)
    old = NOW - dt.timedelta(days=1)
    with uow as work:
        billed_runs(
            work,
            # 51-й с конца: дорогой, в цену не попадает.
            finished(model="tensorix/glm-5.3", usage=spent("9", 1000), created_at=old, updated_at=old),
            *(
                finished(model=("tensorix/glm-5.3", "scaleway/glm-5.3")[n % 2], usage=spent("0.0001", 100))
                for n in range(50)
            ),
            # Без стоимости (Ollama) — мимо.
            finished(model="qwen", usage=spent("0", 1_000_000)),
        )
        work.commit()

    with uow as work:
        assert work.billing.eur_per_token() == Decimal("0.000001")


def test_a_second_transaction_waits_for_the_payer_lock(clean_db) -> None:
    """Проверка баланса одного плательщика идёт по одной: вторая ждёт коммита первой (D3)."""
    payer = new_id()
    locked = threading.Event()
    order: list[str] = []

    def second() -> None:
        locked.wait()
        with SqlAlchemyUnitOfWork(clean_db) as work:
            work.billing.lock_payer(payer)
            order.append("second")
            work.commit()

    thread = threading.Thread(target=second)
    thread.start()
    with SqlAlchemyUnitOfWork(clean_db) as work:
        work.billing.lock_payer(payer)
        locked.set()
        time.sleep(0.3)
        order.append("first")
        work.commit()
    thread.join(timeout=10)

    assert order == ["first", "second"]


def test_another_payer_does_not_wait(clean_db) -> None:
    with SqlAlchemyUnitOfWork(clean_db) as first, SqlAlchemyUnitOfWork(clean_db) as second:
        first.billing.lock_payer(new_id())
        second.session.execute(text("SET LOCAL lock_timeout = '1s'"))
        second.billing.lock_payer(new_id())


def test_reconnect_keeps_an_assigned_payer_and_fills_an_empty_one(uow) -> None:
    alice, bob = a_user("alice"), a_user("bob")
    kept = replace(a_repository(), connected_at=None, payer_user_id=alice.id)
    empty = replace(a_repository(), id=new_id(), provider_id="2", connected_at=None)
    with uow as work:
        work.users.add(alice)
        work.users.add(bob)
        work.repositories.add(kept)
        work.repositories.add(empty)
        work.commit()
    with uow as work:
        first = work.repositories.reconnect(replace(kept, connected_at=LATER, payer_user_id=bob.id))
        second = work.repositories.reconnect(replace(empty, connected_at=LATER, payer_user_id=bob.id))
        work.commit()
    assert (first.payer_user_id, second.payer_user_id) == (alice.id, bob.id)


def test_only_the_payer_drops_payment_and_lists_what_they_pay_for(uow) -> None:
    alice, bob = a_user("alice"), a_user("bob")
    paid_b = replace(a_repository(), full_name="octo/b", payer_user_id=alice.id)
    paid_a = replace(a_repository(), id=new_id(), provider_id="2", full_name="octo/a", payer_user_id=alice.id)
    with uow as work:
        work.users.add(alice)
        work.users.add(bob)
        work.repositories.add(paid_b)
        work.repositories.add(paid_a)
        work.commit()
    with uow as work:
        assert [r.id for r in work.repositories.list_paid_by(alice.id)] == [paid_a.id, paid_b.id]
        assert work.repositories.drop_payer(paid_a.id, bob.id, LATER) is False
        assert work.repositories.drop_payer(paid_a.id, alice.id, LATER) is True
        work.commit()
    with uow as work:
        assert [r.id for r in work.repositories.list_paid_by(alice.id)] == [paid_b.id]
        assert work.repositories.get(paid_a.id).payer_user_id is None
