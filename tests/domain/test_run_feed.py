"""Что уходит в общий поток событий по пачке опроса. Ни часов, ни базы."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.entities import MergeRequest, Repository, ReviewRun, ReviewRunListEntry
from app.domain.enums import MergeRequestState, Provider, TriggerSource
from app.domain.enums import ReviewRunStatus as S
from app.domain.run_feed import (
    RECHECK,
    FeedCursor,
    RunEventType,
    advance_feed,
    open_feed,
    start_feed,
)

CONNECTED = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
BEFORE = CONNECTED - timedelta(hours=1)
REPOSITORY = Repository(
    id=UUID(int=1),
    provider=Provider.GITHUB,
    provider_id="101",
    full_name="octo-org/api",
    default_branch="main",
    auto_review_enabled=True,
    created_at=BEFORE,
    updated_at=BEFORE,
)
MERGE_REQUEST = MergeRequest(
    id=UUID(int=2),
    repository_id=REPOSITORY.id,
    number=42,
    title="Фича",
    description="",
    author="octocat",
    source_branch="feat",
    target_branch="main",
    head_sha="abc123",
    state=MergeRequestState.OPEN,
    created_at=BEFORE,
    updated_at=BEFORE,
)


def _entry(n: int, status: S, updated_at: datetime, created_at: datetime = BEFORE) -> ReviewRunListEntry:
    run = ReviewRun(
        id=UUID(int=100 + n),
        merge_request_id=MERGE_REQUEST.id,
        head_sha="abc123",
        status=status,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=updated_at,
        created_at=created_at,
        updated_at=updated_at,
    )
    return ReviewRunListEntry(run, MERGE_REQUEST, REPOSITORY, findings_count=0)


def _started(*baseline: ReviewRunListEntry):
    return start_feed(open_feed(CONNECTED), baseline)


def _at(seconds: float) -> datetime:
    return CONNECTED + timedelta(seconds=seconds)


def test_the_first_poll_gives_no_events_history_stays_with_the_rest_snapshot() -> None:
    old = _entry(1, S.ANALYSING, _at(-2))
    opened = open_feed(CONNECTED)

    state = start_feed(opened, [old])

    # Повтор той же версии уже ничего не даёт: она учтена, а не отложена.
    _, events = advance_feed(state, [old])
    assert events == []
    assert opened.cursor == FeedCursor(CONNECTED - RECHECK, UUID(int=0))


def test_three_event_types() -> None:
    created = _entry(1, S.QUEUED, _at(1), created_at=_at(1))
    state, events = advance_feed(_started(), [created])
    assert [e.type for e in events] == [RunEventType.CREATED]

    analysing = _entry(1, S.ANALYSING, _at(2), created_at=_at(1))
    state, events = advance_feed(state, [analysing])
    assert [e.type for e in events] == [RunEventType.STATUS_CHANGED]

    completed = _entry(1, S.COMPLETED, _at(3), created_at=_at(1))
    _, events = advance_feed(state, [completed])
    assert [(e.type, e.entry) for e in events] == [(RunEventType.FINISHED, completed)]


def test_a_run_created_before_connecting_is_never_announced_as_created() -> None:
    _, events = advance_feed(_started(), [_entry(1, S.QUEUED, _at(1))])

    assert [e.type for e in events] == [RunEventType.STATUS_CHANGED]


def test_a_run_first_seen_finished_comes_as_finished_not_created() -> None:
    """При переходе в итоговый статус приходит только `run.finished`."""
    failed = _entry(1, S.FAILED, _at(1), created_at=_at(1))

    _, events = advance_feed(_started(), [failed])

    assert [e.type for e in events] == [RunEventType.FINISHED]


def test_run_created_comes_once_per_run() -> None:
    state, _ = advance_feed(_started(), [_entry(1, S.QUEUED, _at(1), created_at=_at(1))])

    _, events = advance_feed(state, [_entry(1, S.QUEUED, _at(2), created_at=_at(1))])

    assert [e.type for e in events] == [RunEventType.STATUS_CHANGED]


def test_progress_without_a_status_change_is_a_status_changed_event() -> None:
    """Клиент делает upsert по id: новая версия строки — событие, даже если статус тот же."""
    state, _ = advance_feed(_started(), [_entry(1, S.ANALYSING, _at(1))])

    _, events = advance_feed(state, [_entry(1, S.ANALYSING, _at(2))])

    assert [e.type for e in events] == [RunEventType.STATUS_CHANGED]


def test_the_same_version_is_sent_once() -> None:
    entry = _entry(1, S.ANALYSING, _at(1))
    state, first = advance_feed(_started(), [entry])

    _, second = advance_feed(state, [entry])

    assert len(first) == 1 and second == []


def test_the_cursor_does_not_lose_a_run_with_the_same_updated_at() -> None:
    """Два прогона с одним `updated_at`: курсор — пара `(updated_at, id)`, а не одно время."""
    first = _entry(1, S.ANALYSING, _at(1))
    second = _entry(2, S.ANALYSING, _at(1))

    state, events = advance_feed(_started(), [first])

    assert state.cursor == FeedCursor(_at(1), first.run.id)
    _, events = advance_feed(state, [second])
    assert [e.entry for e in events] == [second]


def test_a_late_commit_below_the_cursor_still_arrives() -> None:
    """Запись, закоммиченная позже соседней, но с меньшим `updated_at`, — из окна перепроверки."""
    state, _ = advance_feed(_started(), [_entry(2, S.ANALYSING, _at(10))])
    late = _entry(1, S.PUBLISHING, _at(8))

    assert state.recheck_from == _at(10) - RECHECK
    _, events = advance_feed(state, [late])

    assert [e.entry for e in events] == [late]


def test_the_recheck_window_never_reaches_before_the_connection_floor() -> None:
    state = _started()

    assert state.recheck_from == CONNECTED - RECHECK


def test_versions_below_the_recheck_window_are_forgotten() -> None:
    state, _ = advance_feed(_started(), [_entry(1, S.ANALYSING, _at(1))])

    state, _ = advance_feed(state, [_entry(2, S.ANALYSING, _at(60))])

    assert set(state.recent) == {UUID(int=102)}


def test_events_follow_updated_at_order() -> None:
    later = _entry(1, S.ANALYSING, _at(3))
    earlier = _entry(2, S.ANALYSING, _at(2))

    _, events = advance_feed(_started(), [later, earlier])

    assert [e.entry for e in events] == [earlier, later]


def test_a_new_version_of_a_run_seen_at_connect_is_sent() -> None:
    seen = _entry(1, S.ANALYSING, _at(-1))

    _, events = advance_feed(_started(seen), [replace(seen, run=replace(seen.run, updated_at=_at(1)))])

    assert [e.type for e in events] == [RunEventType.STATUS_CHANGED]
