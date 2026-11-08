"""Ручной запуск ревью (`launch_review`, D2 change `manual-review-launch`) на фейках портов.

Каталог установок, VCS, unit of work и очередь — фейки: тест сверяет исход, записи, задачу и
запросы к GitHub, без базы, брокера и сети.
"""

import logging
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.application.ports.unit_of_work import ActiveRunConflict
from app.application.use_cases.connect_repository import UserContext
from app.application.use_cases.launch_review import (
    LaunchRefusal,
    LaunchRequest,
    launch_review,
)
from app.application.use_cases.start_review_run import RunStart
from app.domain.entities import (
    InstallationRepository,
    MergeRequest,
    PRMetadata,
    Repository,
    RepositoryPermissions,
    ReviewRun,
)
from app.domain.enums import MergeRequestState, Provider, ReviewRunStatus, TriggerSource
from app.domain.provider_token import token_from_grant
from app.domain.session import open_session
from app.domain.vcs_errors import VcsError, VcsNotFoundError

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
from ..fakes.installation_catalog import FakeInstallationCatalog
from ..fakes.oauth import EXPIRING_GRANT, FakeOAuthProvider
from ..fakes.run_outcome import RecordingRunOutcome

SESSION_ID = UUID(int=0x5E)
READ = RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)
PUSH = RepositoryPermissions(admin=False, maintain=False, push=True, triage=False)
TRIAGE = RepositoryPermissions(admin=False, maintain=False, push=False, triage=True)
ADMIN = RepositoryPermissions(admin=True, maintain=False, push=False, triage=False)
HEAD = pr_metadata().head_sha
REQUEST = LaunchRequest(
    provider=Provider.GITHUB, provider_repository_id=REGISTERED_PROVIDER_ID, pull_request_number=6
)


def _on_github(permissions: RepositoryPermissions = PUSH, *, private: bool = False) -> InstallationRepository:
    return InstallationRepository(REGISTERED_PROVIDER_ID, REPO_FULL_NAME, "main", permissions, private)


def _signed_in(repository: Repository | None = None) -> FakeUow:
    """Сессия с токеном пользователя и, если передан, зарегистрированный репозиторий."""
    uow = FakeUow()
    uow.sessions.add(open_session(SESSION_ID, UUID(int=1), "cookie", NOW))
    uow.provider_tokens.save(SESSION_ID, token_from_grant(EXPIRING_GRANT, NOW))
    if repository is not None:
        uow.repositories.add(repository)
    return uow


class Case:
    """Сцена запуска: по умолчанию пользователь с ролью push, PR #6 открыт, приложение установлено."""

    def __init__(
        self,
        *,
        uow: FakeUow | None = None,
        catalog: FakeInstallationCatalog | None = None,
        vcs: FakeVcs | None = None,
        queue: FakeQueue | None = None,
        repository: Repository | None = None,
    ) -> None:
        self.repository = repository or a_repository()
        self.uow = uow if uow is not None else _signed_in(self.repository)
        self.catalog = catalog or FakeInstallationCatalog(
            [_on_github()], installation_ids={REPO_FULL_NAME: INSTALLATION_ID}
        )
        self.vcs = vcs or FakeVcs()
        self.queue = queue if queue is not None else FakeQueue()
        self.outcome = RecordingRunOutcome()

    def launch(self, request: LaunchRequest = REQUEST) -> RunStart | LaunchRefusal:
        return launch_review(
            request,
            catalog=self.catalog,
            installations=self.catalog,
            vcs=self.vcs,
            uow=self.uow,
            queue=self.queue,
            user=UserContext(oauth=FakeOAuthProvider(), session_id=SESSION_ID, user_id=UUID(int=1)),
            now=lambda: NOW,
            new_id=uuid4,
            run_outcome=self.outcome,
        )

    def assert_nothing_written(self) -> None:
        assert self.uow.review_runs.added == []
        assert self.uow.merge_requests.added == []
        assert self.queue.jobs == []

    def merge_request(self) -> MergeRequest:
        return self.uow.merge_requests.by_number[(self.repository.id, 6)]


def _existing_run(case: Case, status: ReviewRunStatus, trigger: TriggerSource, head_sha: str = HEAD) -> ReviewRun:
    """Запись PR и прогон на его head — как после вебхука или прежнего запуска."""
    metadata = pr_metadata()
    merge_request = MergeRequest(
        id=uuid4(),
        repository_id=case.repository.id,
        number=6,
        title=metadata.title,
        description="",
        author=metadata.author,
        source_branch=metadata.source_branch,
        target_branch=metadata.target_branch,
        head_sha=head_sha,
        state=MergeRequestState.OPEN,
        created_at=NOW,
        updated_at=NOW,
    )
    case.uow.merge_requests.add(merge_request)
    run = ReviewRun(
        id=uuid4(),
        merge_request_id=merge_request.id,
        head_sha=head_sha,
        base_sha=metadata.base_sha,
        status=status,
        trigger=trigger,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    case.uow.review_runs.add(run)
    return run


def _meta(**changes: Any) -> PRMetadata:
    return replace(pr_metadata(), **changes)


# --- успех ---


def test_launch_records_a_manual_run_and_enqueues_its_job() -> None:
    case = Case()

    start = case.launch()

    assert isinstance(start, RunStart)
    assert start.kind == "success"
    (run,) = case.uow.review_runs.added
    assert start.run == run
    assert run.trigger is TriggerSource.MANUAL
    assert run.status is ReviewRunStatus.QUEUED
    assert (run.head_sha, run.base_sha) == (HEAD, pr_metadata().base_sha)
    (job,) = case.queue.jobs
    assert job.review_run_id == run.id
    assert (job.head_sha, job.base_sha) == (HEAD, pr_metadata().base_sha)
    assert job.action == "manual"
    assert job.event_type == "pull_request"
    assert job.installation_id == INSTALLATION_ID
    assert (job.repository_full_name, job.repository_provider_id) == (REPO_FULL_NAME, REGISTERED_PROVIDER_ID)
    assert case.vcs.calls == [("metadata", REPO_FULL_NAME, 6, INSTALLATION_ID)]


def test_head_comes_from_fresh_metadata() -> None:
    case = Case(vcs=FakeVcs(meta=_meta(head_sha="f" * 40)))

    case.launch()

    (run,) = case.uow.review_runs.added
    assert run.head_sha == "f" * 40
    assert case.queue.jobs[0].head_sha == "f" * 40


def test_a_pull_request_never_seen_is_recorded_from_metadata() -> None:
    case = Case()

    case.launch()

    (merge_request,) = case.uow.merge_requests.added
    metadata = pr_metadata()
    assert (merge_request.number, merge_request.title, merge_request.author) == (6, metadata.title, metadata.author)
    assert merge_request.head_sha == HEAD
    assert case.uow.review_runs.added[0].merge_request_id == merge_request.id


@pytest.mark.parametrize("permissions", [PUSH, TRIAGE, ADMIN, RepositoryPermissions(False, True, False, False)])
def test_any_role_above_read_may_launch(permissions: RepositoryPermissions) -> None:
    case = Case(catalog=FakeInstallationCatalog([_on_github(permissions)]))

    assert isinstance(case.launch(), RunStart)


def test_the_role_is_asked_with_the_user_token() -> None:
    case = Case()

    case.launch()

    assert case.catalog.calls == ["user_repository", "repository_installation_id"]


# --- отказы без записей ---


def test_a_repository_hidden_on_github_is_not_found_without_further_calls() -> None:
    case = Case(catalog=FakeInstallationCatalog([]))

    assert case.launch() is LaunchRefusal.REPOSITORY_NOT_FOUND
    assert case.catalog.calls == ["user_repository"]
    assert case.vcs.calls == []
    case.assert_nothing_written()


def test_a_public_repository_with_read_only_is_not_found() -> None:
    case = Case(catalog=FakeInstallationCatalog([_on_github(READ)]))

    assert case.launch() is LaunchRefusal.REPOSITORY_NOT_FOUND
    assert case.vcs.calls == []


def test_an_unregistered_repository_is_not_found() -> None:
    case = Case(uow=_signed_in())

    assert case.launch() is LaunchRefusal.REPOSITORY_NOT_FOUND
    # GitHub спрошен до базы: ответ не выдаёт, зарегистрирован ли репозиторий.
    assert case.catalog.calls == ["user_repository"]
    case.assert_nothing_written()


def test_a_disconnected_repository_is_not_found() -> None:
    case = Case(repository=replace(a_repository(), connected_at=None))

    assert case.launch() is LaunchRefusal.REPOSITORY_NOT_FOUND
    assert case.vcs.calls == []
    case.assert_nothing_written()


def test_another_provider_is_not_found_without_calls() -> None:
    case = Case()

    refusal = case.launch(replace(REQUEST, provider=Provider.GITLAB))

    assert refusal is LaunchRefusal.REPOSITORY_NOT_FOUND
    assert case.catalog.calls == []


def test_read_on_a_private_repository_requires_a_role_without_asking_the_pull_request() -> None:
    case = Case(catalog=FakeInstallationCatalog([_on_github(READ, private=True)]))

    assert case.launch() is LaunchRefusal.MANAGE_REQUIRED
    assert case.catalog.calls == ["user_repository"]
    assert case.vcs.calls == []
    case.assert_nothing_written()


def test_app_not_installed_refuses_without_metadata() -> None:
    case = Case(catalog=FakeInstallationCatalog([_on_github()], not_installed={REPO_FULL_NAME}))

    assert case.launch() is LaunchRefusal.NOT_INSTALLED
    assert case.vcs.calls == []
    case.assert_nothing_written()


def test_a_missing_pull_request_is_its_own_refusal() -> None:
    case = Case(vcs=FakeVcs(metadata_error=VcsNotFoundError("HTTP 404")))

    assert case.launch() is LaunchRefusal.PULL_REQUEST_NOT_FOUND
    case.assert_nothing_written()


@pytest.mark.parametrize("state", [MergeRequestState.CLOSED, MergeRequestState.MERGED])
def test_a_closed_or_merged_pull_request_is_not_open(state: MergeRequestState) -> None:
    case = Case(vcs=FakeVcs(meta=_meta(state=state)))

    assert case.launch() is LaunchRefusal.PULL_REQUEST_NOT_OPEN
    case.assert_nothing_written()


@pytest.mark.parametrize("association", ["NONE", "FIRST_TIME_CONTRIBUTOR", "CONTRIBUTOR", None])
def test_a_fork_of_an_outsider_is_untrusted_and_logged(
    association: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    """Удалённый форк (`head.repo = null`) адаптер отдаёт как форк — тот же случай."""
    case = Case(vcs=FakeVcs(meta=_meta(from_fork=True, author_association=association)))

    with caplog.at_level(logging.INFO, logger="app.application.use_cases.launch_review"):
        refusal = case.launch()

    assert refusal is LaunchRefusal.PULL_REQUEST_UNTRUSTED
    case.assert_nothing_written()
    (record,) = [r for r in caplog.records if r.name == "app.application.use_cases.launch_review"]
    assert record.__dict__["repo_full_name"] == REPO_FULL_NAME
    assert record.__dict__["pr_number"] == 6
    assert record.__dict__["author_association"] == association


@pytest.mark.parametrize(
    ("from_fork", "association"), [(True, "COLLABORATOR"), (True, "MEMBER"), (False, "CONTRIBUTOR"), (False, None)]
)
def test_a_trusted_pull_request_is_launched(from_fork: bool, association: str | None) -> None:
    case = Case(vcs=FakeVcs(meta=_meta(from_fork=from_fork, author_association=association)))

    assert isinstance(case.launch(), RunStart)
    assert len(case.queue.jobs) == 1


# --- сбои GitHub ---


def test_github_failure_on_the_role_passes_up_before_the_pull_request() -> None:
    case = Case(catalog=FakeInstallationCatalog(error=VcsError("HTTP 503")))

    with pytest.raises(VcsError):
        case.launch()
    assert case.vcs.calls == []
    case.assert_nothing_written()


def test_github_failure_on_the_installation_passes_up_without_metadata() -> None:
    case = Case(
        catalog=FakeInstallationCatalog([_on_github()], installation_id_error=VcsError("HTTP 503"))
    )

    with pytest.raises(VcsError):
        case.launch()
    assert case.vcs.calls == []
    case.assert_nothing_written()


def test_github_failure_on_the_metadata_passes_up() -> None:
    case = Case(vcs=FakeVcs(metadata_error=VcsError("HTTP 502")))

    with pytest.raises(VcsError) as raised:
        case.launch()
    assert not isinstance(raised.value, VcsNotFoundError)
    case.assert_nothing_written()


# --- 3.3: повтор проверенного коммита и активный прогон ---


@pytest.mark.parametrize("trigger", [TriggerSource.WEBHOOK, TriggerSource.MANUAL])
def test_a_reviewed_commit_is_reviewed_again(trigger: TriggerSource) -> None:
    case = Case()
    previous = _existing_run(case, ReviewRunStatus.COMPLETED, trigger)

    start = case.launch()

    assert isinstance(start, RunStart)
    assert start.kind == "success"
    assert start.run is not None and start.run.id != previous.id
    assert start.run.trigger is TriggerSource.MANUAL
    assert len(case.queue.jobs) == 1
    assert case.uow.review_runs.get(previous.id) == previous


def test_a_launch_after_a_failed_run_on_the_head_sets_no_gate() -> None:
    """Блокировка head на приёме (#309) — только у вебхука: ручной запуск ставит gate при
    захвате прогона (#368), а `failure` приёма `launch_review` не обрабатывает."""
    case = Case()
    _existing_run(case, ReviewRunStatus.FAILED, TriggerSource.WEBHOOK)

    start = case.launch()

    assert isinstance(start, RunStart)
    assert start.kind == "success"
    assert case.vcs.checks == []
    assert len(case.queue.jobs) == 1


@pytest.mark.parametrize("trigger", [TriggerSource.WEBHOOK, TriggerSource.MANUAL])
def test_an_active_run_on_the_head_refuses_without_writing(trigger: TriggerSource) -> None:
    case = Case()
    active = _existing_run(case, ReviewRunStatus.ANALYSING, trigger)
    merge_request = case.merge_request()

    assert case.launch() is LaunchRefusal.ALREADY_ACTIVE
    assert case.uow.review_runs.runs == [active]
    assert case.merge_request() == merge_request
    assert case.queue.jobs == []
    assert case.uow.commits == 0


def test_two_launches_in_a_row_refuse_the_second() -> None:
    case = Case()

    assert isinstance(case.launch(), RunStart)
    assert case.launch() is LaunchRefusal.ALREADY_ACTIVE
    assert len(case.queue.jobs) == 1


def test_a_lost_race_on_the_same_commit_is_already_active(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-check победителя не видел, коммит упал на индексе, после отката победитель найден."""
    case = Case()
    winner = _existing_run(case, ReviewRunStatus.QUEUED, TriggerSource.WEBHOOK)
    original = case.uow.review_runs.find_active
    seen = {"n": 0}

    def racing(merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        seen["n"] += 1
        return None if seen["n"] == 1 else original(merge_request_id, head_sha)

    monkeypatch.setattr(case.uow.review_runs, "find_active", racing)
    case.uow.commit_errors.append(ActiveRunConflict("проигравший гонку"))

    assert case.launch() is LaunchRefusal.ALREADY_ACTIVE
    assert case.uow.review_runs.runs == [winner]
    assert case.queue.jobs == []  # задача одна — у победителя, её ставил он


def test_a_race_with_another_commit_writes_over_the_winner() -> None:
    """Победитель гонки нёс другой коммит: активного прогона на наш head нет, есть его
    merge_request — запись повторяется поверх неё, как у вебхука."""
    case = Case()
    other = _existing_run(case, ReviewRunStatus.QUEUED, TriggerSource.WEBHOOK, head_sha="0" * 40)
    winner = case.merge_request()
    case.uow.commit_errors.append(ActiveRunConflict("победитель с другим коммитом"))

    start = case.launch()

    assert isinstance(start, RunStart)
    assert start.kind == "success"
    assert start.merge_request is not None and start.merge_request.id == winner.id
    assert case.merge_request().head_sha == HEAD
    new_run = next(run for run in case.uow.review_runs.runs if run.id != other.id)
    assert (new_run.merge_request_id, new_run.head_sha, new_run.trigger) == (winner.id, HEAD, TriggerSource.MANUAL)
    assert [job.review_run_id for job in case.queue.jobs] == [new_run.id]


# --- 3.4: пауза авторевью и фильтр веток ---


def test_auto_review_off_still_launches_and_sets_no_pause_check() -> None:
    case = Case(repository=replace(a_repository(), auto_review_enabled=False))

    start = case.launch()

    assert isinstance(start, RunStart)
    assert start.run is not None and start.run.trigger is TriggerSource.MANUAL
    assert len(case.queue.jobs) == 1
    assert case.vcs.checks == []
    assert [call[0] for call in case.vcs.calls] == ["metadata"]


def test_branch_filter_is_not_applied() -> None:
    case = Case(repository=replace(a_repository(), branch_filter=("release/*",)))

    assert isinstance(case.launch(), RunStart)
    assert case.uow.merge_requests.added[0].target_branch == "main"


# --- 3.5: сбой постановки ---


class BrokenQueue(FakeQueue):
    def enqueue(self, job: Any) -> None:
        raise RuntimeError("брокер недоступен")


def test_broker_failure_fails_the_run_and_the_next_launch_starts_a_new_one() -> None:
    case = Case(queue=BrokenQueue())

    with pytest.raises(RuntimeError, match="брокер недоступен"):
        case.launch()

    (failed,) = case.uow.review_runs.runs
    assert failed.status is ReviewRunStatus.FAILED
    assert failed.trigger is TriggerSource.MANUAL
    assert failed.failure_reason is not None and "брокер недоступен" in failed.failure_reason
    assert case.outcome.statuses == ["failed"]

    case.queue = FakeQueue()
    start = case.launch()
    assert isinstance(start, RunStart) and start.kind == "success"
    assert len(case.uow.review_runs.added) == 2
    assert len(case.queue.jobs) == 1
