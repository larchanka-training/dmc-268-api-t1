"""Запись события потока в текст SSE: данные `status` — JSON модели `ReviewRunStatusEvent`.

Без базы и сети: событие собирается руками.
"""

from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

from app.api.review_events import format_sse
from app.domain.enums import ReviewRunStatus as S
from app.domain.run_events import PingEvent, StatusEvent

RUN_ID = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 10, 11, 10, 0, tzinfo=UTC)


def test_a_status_event_is_written_as_sse() -> None:
    event = StatusEvent(
        review_run_id=RUN_ID,
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


def test_absent_fields_are_written_as_null() -> None:
    event = StatusEvent(
        review_run_id=RUN_ID,
        status=S.QUEUED,
        last_progress_at=NOW,
        failed_stage=None,
        failure_reason=None,
    )

    assert '"failed_stage":null,"failure_reason":null' in format_sse(event)


def test_time_in_another_zone_is_written_in_utc() -> None:
    moscow = timezone(timedelta(hours=3))
    event = StatusEvent(
        review_run_id=RUN_ID,
        status=S.QUEUED,
        last_progress_at=datetime(2026, 10, 11, 13, 0, tzinfo=moscow),
        failed_stage=None,
        failure_reason=None,
    )

    assert '"last_progress_at":"2026-10-11T10:00:00Z"' in format_sse(event)


def test_a_multiline_reason_stays_on_one_data_line() -> None:
    event = StatusEvent(
        review_run_id=RUN_ID,
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
