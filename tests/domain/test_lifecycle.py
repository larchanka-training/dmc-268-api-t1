from dataclasses import replace
from datetime import UTC, datetime

import pytest

from app.domain.enums import TERMINAL_STATUSES, ReviewRunStatus
from app.domain.enums import ReviewRunStatus as S
from app.domain.lifecycle import advance, next_status

from .conftest import NOW


def test_legal_transition_is_allowed() -> None:
    assert next_status(S.QUEUED, S.BUILDING_CONTEXT).ok


def test_skipping_a_state_is_refused() -> None:
    verdict = next_status(S.QUEUED, S.COMPLETED)
    assert not verdict.ok
    assert "queued" in (verdict.error or "")


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATUSES))
@pytest.mark.parametrize("requested", sorted(ReviewRunStatus))
def test_terminal_states_refuse_everything(terminal: S, requested: S) -> None:
    verdict = next_status(terminal, requested)
    assert not verdict.ok
    assert "terminal" in (verdict.error or "")


def test_advance_stamps_the_supplied_time(run) -> None:
    later = datetime(2026, 9, 9, 12, 5, tzinfo=UTC)
    moved = advance(run, S.BUILDING_CONTEXT, later).unwrap()
    assert moved.status is S.BUILDING_CONTEXT
    assert moved.last_progress_at == later
    assert moved.updated_at == later


def test_advance_leaves_a_terminal_run_untouched(run) -> None:
    done = replace(run, status=S.COMPLETED)
    verdict = advance(done, S.FAILED, NOW)
    assert not verdict.ok
    assert done.status is S.COMPLETED
