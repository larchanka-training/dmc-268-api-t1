from dataclasses import replace
from datetime import UTC, datetime, timedelta

from app.domain.enums import ReviewRunStatus as S
from app.domain.staleness import find_stale

from .conftest import NOW

LIMIT = timedelta(minutes=30)
LATER = datetime(2026, 9, 9, 13, 0, tzinfo=UTC)


def test_run_past_the_limit_is_selected(run) -> None:
    assert find_stale([run], LATER, LIMIT) == [run]


def test_recently_advanced_run_is_skipped(run) -> None:
    fresh = replace(run, last_progress_at=datetime(2026, 9, 9, 12, 50, tzinfo=UTC))
    assert find_stale([fresh], LATER, LIMIT) == []


def test_terminal_run_is_never_selected(run) -> None:
    for terminal in (S.COMPLETED, S.FAILED, S.CANCELLED):
        assert find_stale([replace(run, status=terminal)], LATER, LIMIT) == []


def test_exactly_at_the_limit_is_not_stale_yet(run) -> None:
    assert find_stale([run], NOW + LIMIT, LIMIT) == []
