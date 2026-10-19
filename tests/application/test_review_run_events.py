"""Цикл потока событий — поддельные чтение и ожидание, без базы и настоящего сна."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from itertools import pairwise
from uuid import UUID

from app.application.review_run_events import (
    POLL_SECONDS,
    SESSION_CHECK_SECONDS,
    review_run_events,
)
from app.domain.entities import ReviewRun
from app.domain.enums import ReviewRunStatus as S
from app.domain.enums import TriggerSource
from app.domain.run_events import PING_AFTER_SECONDS, PingEvent, RunEvent, StatusEvent

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)

RUN = ReviewRun(
    id=UUID(int=1),
    merge_request_id=UUID(int=2),
    head_sha="abc123",
    status=S.QUEUED,
    trigger=TriggerSource.WEBHOOK,
    last_progress_at=NOW,
    created_at=NOW,
    updated_at=NOW,
)


class Script:
    """Прогон, каким его увидит каждое следующее чтение; последнее повторяется."""

    def __init__(self, *states: ReviewRun | None) -> None:
        self._states = list(states)
        self.reads = 0
        self.slept: list[float] = []
        # Сессия жива первые `session_checks_alive` проверок, потом — нет.
        self.session_checks_alive: int | None = None
        self.session_checks = 0
        # Время часов теста в момент каждой проверки сессии.
        self.check_times: list[float] = []
        # Часы теста: ожидание и чтение «занимают» время.
        self.now = 0.0
        self.read_cost = 0.0

    async def read(self) -> ReviewRun | None:
        state = self._states[min(self.reads, len(self._states) - 1)]
        self.reads += 1
        self.now += self.read_cost
        return state

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds

    def clock(self) -> float:
        return self.now

    async def session_alive(self) -> bool:
        self.session_checks += 1
        self.check_times.append(self.now)
        return self.session_checks_alive is None or self.session_checks <= self.session_checks_alive


def _collect(script: Script, *, limit: int = 100) -> list[RunEvent]:
    async def run() -> list[RunEvent]:
        # Как в роутере: первое чтение — проверка существования, оно же снимок.
        first = await script.read()
        assert first is not None
        events: list[RunEvent] = []
        async for event in review_run_events(
            first,
            script.read,
            sleep=script.sleep,
            session_alive=script.session_alive,
            clock=script.clock,
        ):
            events.append(event)
            if len(events) == limit:
                break
        return events

    return asyncio.run(run())


def _statuses(events: list[RunEvent]) -> list[S]:
    return [e.status for e in events if isinstance(e, StatusEvent)]


def test_each_status_change_is_sent_and_the_terminal_one_ends_the_stream() -> None:
    script = Script(
        RUN,
        RUN,
        replace(RUN, status=S.ANALYSING),
        replace(RUN, status=S.ANALYSING),
        replace(RUN, status=S.COMPLETED),
    )

    events = _collect(script)

    assert _statuses(events) == [S.QUEUED, S.ANALYSING, S.COMPLETED]
    assert len(events) == 3
    assert script.reads == 5


def test_a_terminal_run_yields_one_snapshot_without_waiting() -> None:
    script = Script(replace(RUN, status=S.FAILED, failed_stage=S.PUBLISHING))

    events = _collect(script)

    assert _statuses(events) == [S.FAILED]
    assert script.slept == []


def test_the_status_is_polled_once_a_second() -> None:
    script = Script(RUN, RUN, replace(RUN, status=S.CANCELLED))

    _collect(script)

    assert script.slept == [1.0, 1.0]


def test_silence_produces_a_ping_and_the_count_starts_over() -> None:
    script = Script(RUN)

    events = _collect(script, limit=3)

    assert events[0] == StatusEvent(
        review_run_id=RUN.id,
        status=S.QUEUED,
        last_progress_at=NOW,
        failed_stage=None,
        failure_reason=None,
    )
    assert events[1:] == [PingEvent(), PingEvent()]
    # Снимок, тишина до первого ping и столько же до второго.
    polls_to_ping = int(PING_AFTER_SECONDS / POLL_SECONDS)
    assert script.reads == 1 + polls_to_ping + polls_to_ping


def test_a_status_change_resets_the_silence() -> None:
    quiet = [RUN] * 10
    script = Script(*quiet, replace(RUN, status=S.ANALYSING))

    events = _collect(script, limit=3)

    assert events[1] == StatusEvent(
        review_run_id=RUN.id,
        status=S.ANALYSING,
        last_progress_at=NOW,
        failed_stage=None,
        failure_reason=None,
    )
    assert events[2] == PingEvent()
    assert script.reads == 11 + int(PING_AFTER_SECONDS / POLL_SECONDS)


def test_the_stream_ends_when_the_run_can_no_longer_be_read() -> None:
    script = Script(RUN, None)

    events = _collect(script)

    assert _statuses(events) == [S.QUEUED]


def test_the_session_is_rechecked_every_five_seconds() -> None:
    script = Script(RUN)
    script.session_checks_alive = 2

    events = _collect(script)

    assert SESSION_CHECK_SECONDS == 5.0
    # Две живые проверки на 5-й и 10-й секунде, третья на 15-й закрывает поток.
    assert script.session_checks == 3
    assert sum(script.slept) == 15 * POLL_SECONDS
    assert _statuses(events) == [S.QUEUED]


def test_a_gone_session_closes_the_stream_without_another_event() -> None:
    script = Script(RUN, replace(RUN, status=S.ANALYSING))
    script.session_checks_alive = 0

    events = _collect(script)

    assert _statuses(events) == [S.QUEUED, S.ANALYSING]
    assert script.session_checks == 1
    assert sum(script.slept) == SESSION_CHECK_SECONDS


def test_the_session_is_rechecked_by_the_clock_even_when_reads_are_slow() -> None:
    script = Script(RUN)
    script.read_cost = 2.0
    script.session_checks_alive = 3

    events = _collect(script)

    # Круг «ожидание 1 с + чтение 2 с»: время чтения копится в интервале, поэтому
    # проверки идут по часам, а не через пять ожиданий (это было бы 15 с).
    # Проверка делается после ожидания, поэтому запаздывает не больше чем
    # на один круг: SESSION_CHECK_SECONDS + POLL_SECONDS + время чтения.
    assert script.session_checks == 4
    # Отсчёт начинается после первого чтения (снимка) в `_collect`.
    points = [script.read_cost, *script.check_times]
    gaps = [b - a for a, b in pairwise(points)]
    assert gaps
    limit = SESSION_CHECK_SECONDS + POLL_SECONDS + script.read_cost
    assert all(SESSION_CHECK_SECONDS <= gap <= limit for gap in gaps), gaps
    assert len(events) == 1
