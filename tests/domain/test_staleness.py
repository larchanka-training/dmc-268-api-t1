from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.entities import ReviewRun
from app.domain.enums import ReviewRunStatus as S
from app.domain.staleness import find_stale

from .conftest import NOW

LIMIT = timedelta(minutes=30)
QUEUED_LIMIT = timedelta(hours=6)
LATER = datetime(2026, 9, 9, 13, 0, tzinfo=UTC)


@pytest.fixture
def working(run: ReviewRun) -> ReviewRun:
    return replace(run, status=S.ANALYSING)


def _stale(runs: list[ReviewRun], now: datetime) -> list[ReviewRun]:
    return find_stale(runs, now, LIMIT, queued_limit=QUEUED_LIMIT)


def test_run_past_the_limit_is_selected(working) -> None:
    assert _stale([working], LATER) == [working]


def test_recently_advanced_run_is_skipped(working) -> None:
    fresh = replace(working, last_progress_at=datetime(2026, 9, 9, 12, 50, tzinfo=UTC))
    assert _stale([fresh], LATER) == []


def test_terminal_run_is_never_selected(run) -> None:
    for terminal in (S.COMPLETED, S.FAILED, S.CANCELLED):
        assert _stale([replace(run, status=terminal)], NOW + timedelta(days=1)) == []


def test_exactly_at_the_limit_is_not_stale_yet(working) -> None:
    assert _stale([working], NOW + LIMIT) == []


def test_every_working_status_uses_the_stale_limit(run) -> None:
    for status in (S.BUILDING_CONTEXT, S.ANALYSING, S.PUBLISHING):
        busy = replace(run, status=status)
        assert _stale([busy], LATER) == [busy]


def test_queued_run_waiting_past_the_stale_limit_is_kept(run) -> None:
    # Хвост очереди длиннее 30 минут: прогон ждёт воркера, а не завис.
    assert run.status is S.QUEUED
    assert _stale([run], NOW + timedelta(hours=5, minutes=59)) == []


def test_queued_run_exactly_at_the_queued_limit_is_not_stale_yet(run) -> None:
    assert _stale([run], NOW + QUEUED_LIMIT) == []


def test_queued_run_past_the_queued_limit_is_selected(run) -> None:
    # Задача так и не дошла до брокера: коммит освобождается, но позже.
    assert _stale([run], NOW + timedelta(hours=6, minutes=1)) == [run]
