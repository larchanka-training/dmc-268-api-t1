"""Что уходит в поток событий прогона. Ни часов, ни сети."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.enums import ReviewRunStatus as S
from app.domain.run_events import PingEvent, StatusEvent, format_sse, next_event

from .conftest import NOW


def test_the_first_event_is_a_snapshot_of_the_current_state(run) -> None:
    analysing = replace(run, status=S.ANALYSING)

    decision = next_event(analysing, last_sent=None, idle_seconds=0)

    assert decision.event == StatusEvent(
        review_run_id=run.id,
        status=S.ANALYSING,
        last_progress_at=NOW,
        failed_stage=None,
        failure_reason=None,
    )
    assert not decision.close


def test_an_unchanged_status_sends_nothing(run) -> None:
    decision = next_event(run, last_sent=S.QUEUED, idle_seconds=3)

    assert decision.event is None
    assert not decision.close


def test_a_new_status_is_sent(run) -> None:
    later = NOW + timedelta(seconds=7)
    moved = replace(run, status=S.BUILDING_CONTEXT, last_progress_at=later)

    decision = next_event(moved, last_sent=S.QUEUED, idle_seconds=1)

    assert isinstance(decision.event, StatusEvent)
    assert decision.event.status is S.BUILDING_CONTEXT
    assert decision.event.last_progress_at == later
    assert not decision.close


@pytest.mark.parametrize(("idle", "pings"), [(13.9, False), (14, True), (40, True)])
def test_silence_is_broken_by_a_ping_within_fifteen_seconds(run, idle: float, pings: bool) -> None:
    decision = next_event(run, last_sent=S.QUEUED, idle_seconds=idle)

    assert (decision.event == PingEvent()) is pings
    assert not decision.close


def test_a_status_change_wins_over_a_due_ping(run) -> None:
    moved = replace(run, status=S.ANALYSING)

    decision = next_event(moved, last_sent=S.BUILDING_CONTEXT, idle_seconds=60)

    assert isinstance(decision.event, StatusEvent)


@pytest.mark.parametrize("terminal", [S.COMPLETED, S.FAILED, S.CANCELLED])
def test_a_terminal_status_is_sent_and_closes_the_stream(run, terminal: S) -> None:
    decision = next_event(
        replace(run, status=terminal), last_sent=S.PUBLISHING, idle_seconds=1
    )

    assert isinstance(decision.event, StatusEvent)
    assert decision.event.status is terminal
    assert decision.close


def test_a_run_that_is_already_terminal_gets_one_snapshot_and_closes(run) -> None:
    decision = next_event(replace(run, status=S.COMPLETED), last_sent=None, idle_seconds=0)

    assert isinstance(decision.event, StatusEvent)
    assert decision.close


def test_a_failure_carries_its_stage_and_reason(run) -> None:
    failed = replace(
        run, status=S.FAILED, failed_stage=S.ANALYSING, failure_reason="модель недоступна"
    )

    decision = next_event(failed, last_sent=S.ANALYSING, idle_seconds=1)

    assert isinstance(decision.event, StatusEvent)
    assert decision.event.failed_stage is S.ANALYSING
    assert decision.event.failure_reason == "модель недоступна"


def test_a_status_event_is_written_as_sse(run) -> None:
    event = StatusEvent(
        review_run_id=run.id,
        status=S.FAILED,
        last_progress_at=datetime(2026, 10, 11, 10, 0, tzinfo=UTC),
        failed_stage=S.ANALYSING,
        failure_reason="модель недоступна",
    )

    assert format_sse(event) == (
        "event: status\n"
        'data: {"review_run_id":"00000000-0000-0000-0000-000000000001",'
        '"status":"failed","last_progress_at":"2026-10-11T10:00:00Z",'
        '"failed_stage":"analysing","failure_reason":"модель недоступна"}\n'
        "\n"
    )


def test_absent_fields_are_written_as_null(run) -> None:
    event = StatusEvent(
        review_run_id=run.id,
        status=S.QUEUED,
        last_progress_at=NOW,
        failed_stage=None,
        failure_reason=None,
    )

    assert '"failed_stage":null,"failure_reason":null' in format_sse(event)


def test_time_in_another_zone_is_written_in_utc(run) -> None:
    moscow = timezone(timedelta(hours=3))
    event = StatusEvent(
        review_run_id=run.id,
        status=S.QUEUED,
        last_progress_at=datetime(2026, 10, 11, 13, 0, tzinfo=moscow),
        failed_stage=None,
        failure_reason=None,
    )

    assert '"last_progress_at":"2026-10-11T10:00:00Z"' in format_sse(event)


def test_a_multiline_reason_stays_on_one_data_line(run) -> None:
    event = StatusEvent(
        review_run_id=run.id,
        status=S.FAILED,
        last_progress_at=NOW,
        failed_stage=None,
        failure_reason="первая\nвторая",
    )

    text = format_sse(event)

    assert text.count("\n") == 3
    assert "первая\\nвторая" in text


def test_a_ping_is_a_named_event_with_empty_data() -> None:
    assert format_sse(PingEvent()) == "event: ping\ndata: {}\n\n"
