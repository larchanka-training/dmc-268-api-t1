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
from app.domain.entities import Repository
from app.domain.enums import Provider

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

    assert pause_check(repository) is expected
