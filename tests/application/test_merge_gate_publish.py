"""Check run на head, когда ревью по вебхуку не идёт."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.merge_gate_publish import (
    AUTO_REVIEW_OFF_CHECK,
    DISABLED_REVIEW_CHECK,
    head_run_in_charge,
    pause_check,
    publish_pause_check,
)
from app.application.ports.vcs_review import MergeGateCheck
from app.domain.entities import MergeRequest, Repository, ReviewRun
from app.domain.enums import MergeRequestState, Provider, ReviewRunStatus, TriggerSource
from app.domain.vcs_errors import VcsTransientError

from ..fakes.unit_of_work import FakeUnitOfWork, add_manual_run_with_critical_thread
from ..fakes.vcs_gateway import RecordingVcsGateway

_NOW = datetime(2026, 10, 31, tzinfo=UTC)


@pytest.mark.parametrize(
    ("connected_at", "auto_review_enabled", "expected"),
    [
        (_NOW, True, None),
        (_NOW, False, AUTO_REVIEW_OFF_CHECK),
        (None, True, DISABLED_REVIEW_CHECK),
        (None, False, DISABLED_REVIEW_CHECK),
    ],
)
def test_disconnection_outranks_auto_review_setting_in_pause_check(
    connected_at: datetime | None, auto_review_enabled: bool, expected: MergeGateCheck | None
) -> None:
    repository = Repository(
        id=UUID(int=1),
        provider=Provider.GITHUB,
        provider_id="987654",
        full_name="owner/repo",
        default_branch="main",
        auto_review_enabled=auto_review_enabled,
        created_at=_NOW,
        updated_at=_NOW,
        connected_at=connected_at,
    )

    assert pause_check(repository, None) is expected


def _repository(*, connected: bool, auto_review_enabled: bool) -> Repository:
    return Repository(
        id=UUID(int=1),
        provider=Provider.GITHUB,
        provider_id="987654",
        full_name="owner/repo",
        default_branch="main",
        auto_review_enabled=auto_review_enabled,
        created_at=_NOW,
        updated_at=_NOW,
        connected_at=_NOW if connected else None,
    )


def _head_run(trigger: TriggerSource, status: ReviewRunStatus) -> ReviewRun:
    return ReviewRun(
        id=UUID(int=2),
        merge_request_id=UUID(int=3),
        head_sha="head1",
        status=status,
        trigger=trigger,
        last_progress_at=_NOW,
        created_at=_NOW,
        updated_at=_NOW,
    )


_MANUAL = TriggerSource.MANUAL
_WEBHOOK = TriggerSource.WEBHOOK
_S = ReviewRunStatus


@pytest.mark.parametrize(
    ("connected", "auto_review_enabled", "head_run", "expected"),
    [
        # Выключенный репозиторий важнее ручного прогона (D11).
        (False, False, _head_run(_MANUAL, _S.COMPLETED), DISABLED_REVIEW_CHECK),
        (False, True, _head_run(_MANUAL, _S.COMPLETED), DISABLED_REVIEW_CHECK),
        # Пауза без прогона и при вебхуковом — check паузы, как в #329.
        (True, False, None, AUTO_REVIEW_OFF_CHECK),
        (True, False, _head_run(_WEBHOOK, _S.COMPLETED), AUTO_REVIEW_OFF_CHECK),
        # Ручной прогон на паузе ставит свой gate, пока не упал и не отменён.
        (True, False, _head_run(_MANUAL, _S.COMPLETED), None),
        (True, False, _head_run(_MANUAL, _S.ANALYSING), None),
        (True, False, _head_run(_MANUAL, _S.QUEUED), None),
        (True, False, _head_run(_MANUAL, _S.FAILED), AUTO_REVIEW_OFF_CHECK),
        (True, False, _head_run(_MANUAL, _S.CANCELLED), AUTO_REVIEW_OFF_CHECK),
        # Без паузы — ничего.
        (True, True, _head_run(_MANUAL, _S.COMPLETED), None),
        (True, True, _head_run(_WEBHOOK, _S.FAILED), None),
    ],
)
def test_manual_head_run_keeps_its_merge_gate_on_auto_review_pause(
    connected: bool,
    auto_review_enabled: bool,
    head_run: ReviewRun | None,
    expected: MergeGateCheck | None,
) -> None:
    repository = _repository(connected=connected, auto_review_enabled=auto_review_enabled)

    assert pause_check(repository, head_run) is expected



_MR_ID = UUID(int=3)
_PR_NUMBER = 7


def _paused_pr() -> FakeUnitOfWork:
    """PR без прогона на head у репозитория с `auto_review_enabled = false`."""
    uow = FakeUnitOfWork()
    uow.repositories.add(_repository(connected=True, auto_review_enabled=False))
    uow.merge_requests.add(
        MergeRequest(
            id=_MR_ID,
            repository_id=UUID(int=1),
            number=_PR_NUMBER,
            title="t",
            description="",
            author="a",
            source_branch="feat",
            target_branch="main",
            head_sha="head1",
            state=MergeRequestState.OPEN,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    return uow


def _manual_run_during_pause(
    uow: FakeUnitOfWork, status: ReviewRunStatus, *, lost: bool = False
) -> RecordingVcsGateway:
    """GitHub, чей POST check паузы задержан: тем временем ручной прогон дошёл до `status`;
    при `lost` ответ на дошедший POST теряется."""

    def manual_run_moves_on() -> None:
        add_manual_run_with_critical_thread(
            uow,
            merge_request_id=_MR_ID,
            head_sha="head1",
            status=status,
            at=_NOW + timedelta(minutes=1),
        )
        if lost:
            raise VcsTransientError("GitHub API: HTTP 502")

    return RecordingVcsGateway(during_first_check=manual_run_moves_on)


def _publish_pause(uow: FakeUnitOfWork, vcs: RecordingVcsGateway) -> None:
    publish_pause_check(
        uow,
        vcs,
        AUTO_REVIEW_OFF_CHECK,
        repository_id=UUID(int=1),
        pr_number=_PR_NUMBER,
        repo_full_name="owner/repo",
        head_sha="head1",
        installation_id=1,
    )


@pytest.mark.parametrize("status", [ReviewRunStatus.COMPLETED, ReviewRunStatus.PUBLISHING])
def test_a_delayed_pause_check_returns_the_gate_of_a_manual_run(status: ReviewRunStatus) -> None:
    """D11, ревью #335: check паузы пришёл на GitHub после `failure` ручного прогона —
    после POST прогон перечитан, его `failure` выставлен снова, а не оставлен `success` паузы."""
    uow = _paused_pr()
    vcs = _manual_run_during_pause(uow, status)

    _publish_pause(uow, vcs)

    assert [c.conclusion for c in vcs.checks] == ["success", "failure"]
    assert vcs.checks[0] == AUTO_REVIEW_OFF_CHECK


def test_a_lost_pause_check_response_still_returns_the_gate_and_raises() -> None:
    """Ответ на POST паузы потерян, а сам POST дошёл: gate ручного прогона возвращён,
    сбой — вызывающему."""
    uow = _paused_pr()
    vcs = _manual_run_during_pause(uow, ReviewRunStatus.COMPLETED, lost=True)

    with pytest.raises(VcsTransientError):
        _publish_pause(uow, vcs)

    assert [c.conclusion for c in vcs.checks] == ["success", "failure"]


@pytest.mark.parametrize(
    "status", [ReviewRunStatus.QUEUED, ReviewRunStatus.ANALYSING, ReviewRunStatus.FAILED]
)
def test_a_pause_check_leaves_the_gate_to_a_run_not_yet_publishing(status: ReviewRunStatus) -> None:
    """Прогон до публикации выставит gate сам, позже POST паузы; упавший — пауза в силе."""
    uow = _paused_pr()
    vcs = _manual_run_during_pause(uow, status)

    _publish_pause(uow, vcs)

    assert vcs.checks == [AUTO_REVIEW_OFF_CHECK]


def _run_on_head(
    run_id: int, trigger: TriggerSource, status: ReviewRunStatus, *, minute: int
) -> ReviewRun:
    at = _NOW + timedelta(minutes=minute)
    return ReviewRun(
        id=UUID(int=run_id),
        merge_request_id=_MR_ID,
        head_sha="head1",
        status=status,
        trigger=trigger,
        last_progress_at=at,
        created_at=at,
        updated_at=at,
    )


@pytest.mark.parametrize(
    ("connected", "auto_review_enabled", "runs", "in_charge", "expected"),
    [
        # Повторный ручной прогон упал или отменён — за head снова завершённый прежний.
        pytest.param(
            True,
            False,
            [(40, _MANUAL, _S.COMPLETED), (41, _MANUAL, _S.FAILED)],
            40,
            None,
            id="failed-relaunch",
        ),
        pytest.param(
            True,
            False,
            [(40, _MANUAL, _S.COMPLETED), (41, _MANUAL, _S.CANCELLED)],
            40,
            None,
            id="cancelled-relaunch",
        ),
        # Из двух завершённых ручных — последний.
        pytest.param(
            True,
            False,
            [
                (39, _MANUAL, _S.COMPLETED),
                (40, _MANUAL, _S.COMPLETED),
                (41, _MANUAL, _S.FAILED),
            ],
            40,
            None,
            id="latest-completed-manual",
        ),
        # Ручной в работе отвечает за head сам.
        pytest.param(
            True,
            False,
            [(40, _MANUAL, _S.COMPLETED), (41, _MANUAL, _S.ANALYSING)],
            41,
            None,
            id="relaunch-in-progress",
        ),
        # Вебхуковый до паузы ручного gate не даёт — check паузы.
        pytest.param(
            True,
            False,
            [(40, _WEBHOOK, _S.COMPLETED), (41, _MANUAL, _S.FAILED)],
            41,
            AUTO_REVIEW_OFF_CHECK,
            id="webhook-before-pause",
        ),
        pytest.param(
            True,
            False,
            [(40, _WEBHOOK, _S.COMPLETED)],
            40,
            AUTO_REVIEW_OFF_CHECK,
            id="only-webhook",
        ),
        # Ревью #335: последний — вебхуковый после ручного (например, после смены целевой
        # ветки), — за head отвечает он, а на паузе это check паузы, не gate ручного.
        pytest.param(
            True,
            False,
            [(40, _MANUAL, _S.COMPLETED), (41, _WEBHOOK, _S.COMPLETED)],
            41,
            AUTO_REVIEW_OFF_CHECK,
            id="webhook-after-manual",
        ),
        pytest.param(
            True,
            False,
            [(40, _MANUAL, _S.COMPLETED), (41, _WEBHOOK, _S.FAILED)],
            41,
            AUTO_REVIEW_OFF_CHECK,
            id="failed-webhook-after-manual",
        ),
        # Выключенный репозиторий важнее завершённого ручного.
        pytest.param(
            False,
            False,
            [(40, _MANUAL, _S.COMPLETED), (41, _MANUAL, _S.FAILED)],
            41,
            DISABLED_REVIEW_CHECK,
            id="disconnected",
        ),
        # Без паузы — последний прогон, как раньше.
        pytest.param(
            True,
            True,
            [(40, _MANUAL, _S.COMPLETED), (41, _MANUAL, _S.FAILED)],
            41,
            None,
            id="auto-review-on",
        ),
        pytest.param(True, False, [], None, AUTO_REVIEW_OFF_CHECK, id="no-run"),
    ],
)
def test_head_run_in_charge_returns_the_completed_manual_run_after_a_failed_relaunch(
    connected: bool,
    auto_review_enabled: bool,
    runs: list[tuple[int, TriggerSource, ReviewRunStatus]],
    in_charge: int | None,
    expected: MergeGateCheck | None,
) -> None:
    """Ревью #335: на паузе авторевью упавший повторный ручной прогон не забирает head у
    завершённого прежнего; вебхуковый прогон до паузы и выключенный репозиторий — как раньше."""
    repository = _repository(
        connected=connected, auto_review_enabled=auto_review_enabled
    )
    uow = FakeUnitOfWork()
    for minute, (run_id, trigger, status) in enumerate(runs):
        uow.review_runs.add(_run_on_head(run_id, trigger, status, minute=minute))

    with uow:
        head_run = head_run_in_charge(uow, repository, _MR_ID, "head1")

    assert (head_run.id if head_run else None) == (
        UUID(int=in_charge) if in_charge else None
    )
    assert pause_check(repository, head_run) is expected


def test_head_run_in_charge_without_a_repository_is_the_latest_run() -> None:
    """Без записи репозитория паузы нет — за head отвечает последний прогон."""
    uow = FakeUnitOfWork()
    uow.review_runs.add(_run_on_head(40, _MANUAL, _S.COMPLETED, minute=0))
    uow.review_runs.add(_run_on_head(41, _MANUAL, _S.FAILED, minute=1))

    with uow:
        head_run = head_run_in_charge(uow, None, _MR_ID, "head1")

    assert head_run is not None
    assert head_run.id == UUID(int=41)


def test_a_delayed_pause_check_returns_the_gate_of_a_manual_run_after_its_relaunch_failed() -> (
    None
):
    """Ревью #335: пока GitHub принимал check паузы, ручной прогон опубликовал `failure`,
    а его повтор упал — после POST на head снова gate завершённого прогона."""
    uow = _paused_pr()

    def manual_run_completes_and_its_relaunch_fails() -> None:
        add_manual_run_with_critical_thread(
            uow,
            merge_request_id=_MR_ID,
            head_sha="head1",
            status=ReviewRunStatus.COMPLETED,
            at=_NOW + timedelta(minutes=1),
        )
        uow.review_runs.add(_run_on_head(41, _MANUAL, _S.FAILED, minute=2))

    vcs = RecordingVcsGateway(
        during_first_check=manual_run_completes_and_its_relaunch_fails
    )

    _publish_pause(uow, vcs)

    assert vcs.checks[0] == AUTO_REVIEW_OFF_CHECK
    assert [c.conclusion for c in vcs.checks] == ["success", "failure"]
