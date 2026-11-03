"""Check run на head, когда ревью по вебхуку не идёт."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.merge_gate_publish import (
    AUTO_REVIEW_OFF_CHECK,
    DISABLED_REVIEW_CHECK,
    pause_check,
)
from app.application.ports.vcs_review import MergeGateCheck
from app.domain.entities import Repository, ReviewRun
from app.domain.enums import Provider, ReviewRunStatus, TriggerSource

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
