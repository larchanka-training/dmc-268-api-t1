"""Выметание зависших прогонов — фейковый `UnitOfWork`, без БД."""

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.merge_gate_publish import (
    AUTO_REVIEW_OFF_CHECK,
    build_failed_review_check,
)
from app.application.ports.vcs_review import MergeGateCheck
from app.application.stale_sweep import sweep_stale_runs
from app.domain.entities import MergeRequest, RepositorySettings, ReviewRun
from app.domain.enums import (
    FindingSeverity,
    MergeRequestState,
    ReviewRunStage,
    ReviewRunStatus,
    ReviewRunStepStatus,
    TriggerSource,
)
from app.domain.run_steps import finish_step, start_step
from app.domain.vcs_errors import VcsError, VcsTransientError

from ..fakes.installation_catalog import FakeInstallationCatalog
from ..fakes.run_outcome import RecordingRunOutcome
from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.vcs_gateway import RecordingVcsGateway
from ..fakes.webhooks import a_repository

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)
LIMIT = timedelta(minutes=30)
QUEUED_LIMIT = timedelta(hours=6)


def _run(n: int, status: ReviewRunStatus, idle: timedelta) -> ReviewRun:
    moment = NOW - idle
    return ReviewRun(
        id=UUID(int=n),
        merge_request_id=UUID(int=99),
        head_sha=f"sha{n}",
        status=status,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=moment,
        created_at=moment,
        updated_at=moment,
    )


def test_a_run_without_progress_beyond_the_limit_is_failed() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.BUILDING_CONTEXT, timedelta(hours=1)))

    swept = sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT)

    run = uow.review_runs.get(UUID(int=1))
    assert swept == 1
    assert run is not None
    assert run.status == ReviewRunStatus.FAILED
    assert run.failure_reason
    assert run.last_progress_at == NOW


def test_a_swept_run_keeps_the_stage_it_stalled_in() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.PUBLISHING, timedelta(hours=1)))

    sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT)

    run = uow.review_runs.get(UUID(int=1))
    assert run is not None
    assert run.failed_stage is ReviewRunStatus.PUBLISHING


def test_a_run_within_the_limit_and_a_terminal_run_are_left_alone() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(minutes=5)))
    uow.review_runs.add(_run(2, ReviewRunStatus.COMPLETED, timedelta(hours=5)))

    swept = sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT)

    assert swept == 0
    assert uow.review_runs.get(UUID(int=1)).status == ReviewRunStatus.ANALYSING  # type: ignore[union-attr]
    assert uow.review_runs.get(UUID(int=2)).status == ReviewRunStatus.COMPLETED  # type: ignore[union-attr]


def test_each_swept_run_is_counted_as_failed_once() -> None:
    """Счётчик исходов пишет выметатель: `failed` — по одному на каждый перевод."""
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    uow.review_runs.add(_run(2, ReviewRunStatus.PUBLISHING, timedelta(hours=2)))
    # Свежий и терминальный прогоны выметению не подлежат — записей у них нет.
    uow.review_runs.add(_run(3, ReviewRunStatus.ANALYSING, timedelta(minutes=5)))
    uow.review_runs.add(_run(4, ReviewRunStatus.COMPLETED, timedelta(hours=5)))
    recorder = RecordingRunOutcome()

    swept = sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT, run_outcome=recorder)

    assert swept == 2
    assert recorder.statuses == ["failed", "failed"]


def test_a_swept_run_closes_the_step_its_worker_left_running() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    started = NOW - timedelta(hours=1)
    done = finish_step(
        start_step(UUID(int=1), ReviewRunStage.BUILDING_CONTEXT, started_at=started, step_id=UUID(int=10)),
        duration_seconds=2.0,
        tokens_used=None,
    )
    uow.review_run_steps.add(done)
    uow.review_run_steps.add(
        start_step(UUID(int=1), ReviewRunStage.ANALYSING, started_at=started, step_id=UUID(int=11))
    )

    sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT)

    kept, closed = uow.review_run_steps.list_for_run(UUID(int=1))
    assert kept == done
    assert closed.status is ReviewRunStepStatus.FAILED
    assert closed.error_code == "REVIEW_RUN_STALE"
    assert closed.duration_seconds == 3600.0


def test_a_failure_closing_the_steps_does_not_undo_the_sweep() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    uow.review_run_steps.add(
        start_step(UUID(int=1), ReviewRunStage.ANALYSING, started_at=NOW, step_id=UUID(int=11))
    )

    def broken(step: object) -> None:
        raise ConnectionError("база недоступна")

    uow.review_run_steps.update = broken  # type: ignore[method-assign]

    assert sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT) == 1
    assert uow.review_runs.get(UUID(int=1)).status == ReviewRunStatus.FAILED  # type: ignore[union-attr]


def test_a_queued_run_is_failed_only_after_the_queued_limit() -> None:
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run(1, ReviewRunStatus.QUEUED, timedelta(hours=1)))
    uow.review_runs.add(_run(2, ReviewRunStatus.QUEUED, timedelta(hours=7)))

    swept = sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT)

    assert swept == 1
    waiting = uow.review_runs.get(UUID(int=1))
    lost = uow.review_runs.get(UUID(int=2))
    assert waiting is not None and waiting.status == ReviewRunStatus.QUEUED
    assert lost is not None and lost.status == ReviewRunStatus.FAILED
    assert lost.failure_reason == "нет прогресса дольше 6:00:00"


def test_a_run_that_moved_after_the_read_is_skipped_and_the_rest_are_swept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Прогон продвинулся между чтением и записью: живой остаётся, остальные выметаются."""
    uow = FakeUnitOfWork()
    moved = _run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1))
    other = _run(2, ReviewRunStatus.ANALYSING, timedelta(hours=1))
    uow.review_runs.add(moved)
    uow.review_runs.add(other)
    read = uow.review_runs.list_unfinished

    def read_then_progress() -> list[ReviewRun]:
        runs = read()
        uow.review_runs.update(replace(moved, last_progress_at=NOW))
        uow.commit()
        return runs

    monkeypatch.setattr(uow.review_runs, "list_unfinished", read_then_progress)
    recorder = RecordingRunOutcome()

    swept = sweep_stale_runs(uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT, run_outcome=recorder)

    assert swept == 1
    assert recorder.statuses == ["failed"]
    assert uow.review_runs.get(moved.id).status is ReviewRunStatus.ANALYSING  # type: ignore[union-attr]
    assert uow.review_runs.get(other.id).status is ReviewRunStatus.FAILED  # type: ignore[union-attr]


# Merge gate выметенного прогона (#331): прогоны `_run` - на PR 99 репозитория 9.
REPO_ID = UUID(int=9)


def _seed_pull_request(uow: FakeUnitOfWork) -> str:
    repository = replace(a_repository(), id=REPO_ID)
    uow.repositories.add(repository)
    uow.merge_requests.add(
        MergeRequest(
            id=UUID(int=99),
            repository_id=REPO_ID,
            number=7,
            title="t",
            description="",
            author="a",
            source_branch="feat",
            target_branch="develop",
            head_sha="sha1",
            state=MergeRequestState.OPEN,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    return repository.full_name


def _reviewed_head(uow: FakeUnitOfWork, n: int, *, head: str = "sha1") -> None:
    """Прогон `n` по вебхуку завершил ревью head раньше выметаемого."""
    earlier = _run(n, ReviewRunStatus.COMPLETED, timedelta(hours=3))
    uow.review_runs.add(replace(earlier, head_sha=head))


@dataclass
class _GateTargets(RecordingVcsGateway):
    """Куда ушёл check run: репозиторий, head и номер установки."""

    targets: list[tuple[str, str, int]] = field(default_factory=list)

    def set_merge_gate_check(
        self, repo_full_name: str, head_sha: str, installation_id: int, check: MergeGateCheck
    ) -> None:
        self.targets.append((repo_full_name, head_sha, installation_id))
        super().set_merge_gate_check(repo_full_name, head_sha, installation_id, check)


def _sweep(uow: FakeUnitOfWork, vcs: RecordingVcsGateway, installations: FakeInstallationCatalog) -> int:
    return sweep_stale_runs(
        uow, now=NOW, limit=LIMIT, queued_limit=QUEUED_LIMIT, vcs=vcs, installations=installations
    )


def test_a_swept_run_after_an_earlier_review_blocks_the_head() -> None:
    """Воркер умер на head, где уже было ревью: на head мог остаться прежний `success`."""
    uow = FakeUnitOfWork()
    full_name = _seed_pull_request(uow)
    _reviewed_head(uow, 5)
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    vcs = _GateTargets()
    installations = FakeInstallationCatalog(installation_ids={full_name: 77})

    assert _sweep(uow, vcs, installations) == 1

    assert vcs.checks == [build_failed_review_check()]
    assert vcs.targets == [(full_name, "sha1", 77)]


def test_a_swept_run_on_pause_sets_the_pause_check() -> None:
    uow = FakeUnitOfWork()
    _seed_pull_request(uow)
    uow.repositories.update_settings(REPO_ID, RepositorySettings(False, (), FindingSeverity.CRITICAL), NOW)
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    vcs = RecordingVcsGateway()

    _sweep(uow, vcs, FakeInstallationCatalog())

    assert vcs.checks == [AUTO_REVIEW_OFF_CHECK]


def test_a_swept_first_review_of_the_head_sets_no_check() -> None:
    uow = FakeUnitOfWork()
    _seed_pull_request(uow)
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    vcs = RecordingVcsGateway()

    assert _sweep(uow, vcs, FakeInstallationCatalog()) == 1

    assert vcs.checks == []


def test_a_swept_run_superseded_on_its_head_sets_no_check() -> None:
    """За head отвечает новый прогон: его gate не трогаем."""
    uow = FakeUnitOfWork()
    _seed_pull_request(uow)
    _reviewed_head(uow, 5)
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    uow.review_runs.add(replace(_run(2, ReviewRunStatus.QUEUED, timedelta(minutes=1)), head_sha="sha1"))
    vcs = RecordingVcsGateway()

    assert _sweep(uow, vcs, FakeInstallationCatalog()) == 1

    assert vcs.checks == []


@pytest.mark.parametrize(
    ("installations", "vcs"),
    [
        pytest.param(
            FakeInstallationCatalog(installation_id_error=VcsError("GitHub недоступен")),
            RecordingVcsGateway(),
            id="catalog-error",
        ),
        pytest.param(
            FakeInstallationCatalog(not_installed={a_repository().full_name}),
            RecordingVcsGateway(),
            id="not-installed",
        ),
        pytest.param(
            FakeInstallationCatalog(),
            RecordingVcsGateway(check_error=VcsError("GitHub недоступен")),
            id="check-error",
        ),
    ],
)
def test_a_github_failure_does_not_stop_the_sweep(
    installations: FakeInstallationCatalog, vcs: RecordingVcsGateway
) -> None:
    """Gate - best-effort: прогоны выметаются все, перевод в `failed` не откатывается."""
    uow = FakeUnitOfWork()
    _seed_pull_request(uow)
    _reviewed_head(uow, 5)
    _reviewed_head(uow, 6, head="sha2")
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    uow.review_runs.add(_run(2, ReviewRunStatus.PUBLISHING, timedelta(hours=2)))
    recorder = RecordingRunOutcome()

    swept = sweep_stale_runs(
        uow,
        now=NOW,
        limit=LIMIT,
        queued_limit=QUEUED_LIMIT,
        run_outcome=recorder,
        vcs=vcs,
        installations=installations,
    )

    assert swept == 2
    assert recorder.statuses == ["failed", "failed"]
    assert installations.calls == ["repository_installation_id"] * 2
    for n in (1, 2):
        assert uow.review_runs.get(UUID(int=n)).status is ReviewRunStatus.FAILED  # type: ignore[union-attr]
    assert vcs.checks == []


def test_github_down_stops_gate_lookups_for_the_rest_of_the_pass() -> None:
    """Проход идёт в consume-цикле: после временного сбоя GitHub следующие прогоны не
    ждут таймаутов, но выметаются все."""
    uow = FakeUnitOfWork()
    _seed_pull_request(uow)
    _reviewed_head(uow, 5)
    _reviewed_head(uow, 6, head="sha2")
    uow.review_runs.add(_run(1, ReviewRunStatus.ANALYSING, timedelta(hours=1)))
    uow.review_runs.add(_run(2, ReviewRunStatus.PUBLISHING, timedelta(hours=2)))
    installations = FakeInstallationCatalog(installation_id_error=VcsTransientError("лимит GitHub"))

    assert _sweep(uow, RecordingVcsGateway(), installations) == 2

    assert installations.calls == ["repository_installation_id"]
