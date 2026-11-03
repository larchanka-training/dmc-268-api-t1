"""Check run на head, когда ревью по вебхуку не идёт."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.application.merge_gate_publish import (
    AUTO_REVIEW_OFF_CHECK,
    DISABLED_REVIEW_CHECK,
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
