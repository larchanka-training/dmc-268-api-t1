"""Цикл общего потока событий — поддельные сигналы изменений, чтение и часы, без базы и настоящего ожидания."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from uuid import UUID

from app.application.ports.run_changes import RunChangesLost
from app.application.run_feed_events import (
    SESSION_CHECK_SECONDS,
    FeedItem,
    run_feed_events,
    visible_repository_ids,
)
from app.domain.entities import (
    InstallationRepository,
    MergeRequest,
    Repository,
    RepositoryPermissions,
    ReviewRun,
    ReviewRunListEntry,
)
from app.domain.enums import MergeRequestState, Provider, TriggerSource
from app.domain.enums import ReviewRunStatus as S
from app.domain.run_events import PING_AFTER_SECONDS, PingEvent
from app.domain.run_feed import (
    BATCH_LIMIT,
    RECHECK,
    RunEventType,
    RunFeedEvent,
    RunFeedState,
    open_feed,
)

from ..fakes.unit_of_work import FakeUnitOfWork

CONNECTED = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
READ = RepositoryPermissions(admin=False, maintain=False, push=False, triage=False)
REPOSITORY = Repository(
    id=UUID(int=1),
    provider=Provider.GITHUB,
    provider_id="101",
    full_name="octo-org/api",
    default_branch="main",
    auto_review_enabled=True,
    created_at=CONNECTED,
    updated_at=CONNECTED,
    connected_at=CONNECTED,
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
    created_at=CONNECTED,
    updated_at=CONNECTED,
)


def _entry(status: S, seconds: float) -> ReviewRunListEntry:
    at = CONNECTED + timedelta(seconds=seconds)
    run = ReviewRun(
        id=UUID(int=3),
        merge_request_id=MERGE_REQUEST.id,
        head_sha="abc123",
        status=status,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=at,
        created_at=CONNECTED - timedelta(hours=1),
        updated_at=at,
    )
    return ReviewRunListEntry(run, MERGE_REQUEST, REPOSITORY, findings_count=0)


class Script:
    """Пачки, которые вернут следующие чтения; дальше — пусто. Сигналы — по расписанию.

    Часы идут ожиданием: сигнал в момент `t` будит ожидание, если `t` наступает до
    его таймаута; иначе ожидание длится весь таймаут и возвращает `False`.
    """

    def __init__(self, *batches: list[ReviewRunListEntry], changes_at: tuple[float, ...] = ()) -> None:
        self._batches = list(batches)
        self._changes_at = sorted(changes_at)
        self.lost_at: float | None = None
        self.reads = 0
        self.recheck_from: list[datetime] = []
        self.cursors: list[datetime] = []
        self.now = 0.0
        self.session_checks = 0
        self.session_checks_alive: int | None = None
        self.check_times: list[float] = []
        # Сколько «секунд» занимает одно чтение пачки.
        self.read_cost = 0.0

    async def read(self, state: RunFeedState) -> list[ReviewRunListEntry]:
        self.reads += 1
        self.recheck_from.append(state.recheck_from)
        self.cursors.append(state.cursor.updated_at)
        self.now += self.read_cost
        return self._batches.pop(0) if self._batches else []

    async def wait_change(self, timeout: float) -> bool:
        deadline = self.now + timeout
        next_change = self._changes_at[0] if self._changes_at else None
        if self.lost_at is not None and self.lost_at <= deadline and (next_change is None or self.lost_at < next_change):
            self.now = max(self.now, self.lost_at)
            raise RunChangesLost
        if self._changes_at and self._changes_at[0] <= deadline:
            self.now = max(self.now, self._changes_at.pop(0))
            # Сигналы, накопившиеся к этому моменту, — один `True`.
            while self._changes_at and self._changes_at[0] <= self.now:
                self._changes_at.pop(0)
            return True
        self.now = deadline
        return False

    def clock(self) -> float:
        return self.now

    async def session_alive(self) -> bool:
        self.session_checks += 1
        self.check_times.append(self.now)
        return self.session_checks_alive is None or self.session_checks <= self.session_checks_alive


def _collect(script: Script, *, limit: int) -> list[FeedItem]:
    async def run() -> list[FeedItem]:
        items: list[FeedItem] = []
        async for item in run_feed_events(
            open_feed(CONNECTED),
            script.read,
            wait_change=script.wait_change,
            session_alive=script.session_alive,
            clock=script.clock,
        ):
            items.append(item)
            if len(items) >= limit:
                break
        return items

    return asyncio.run(run())


def test_each_change_signal_reads_one_batch_and_turns_it_into_events() -> None:
    analysing = _entry(S.ANALYSING, 1)
    completed = _entry(S.COMPLETED, 2)
    script = Script([analysing], [completed], changes_at=(1.0, 2.5))

    items = _collect(script, limit=2)

    assert items == [
        RunFeedEvent(RunEventType.STATUS_CHANGED, analysing),
        RunFeedEvent(RunEventType.FINISHED, completed),
    ]
    assert script.reads == 2
    assert script.now == 2.5


def test_silence_does_not_read_the_database() -> None:
    """Без сигналов база не читается: минута тишины — ноль чтений, только ping."""
    script = Script()

    items = _collect(script, limit=4)

    assert items == [PingEvent()] * 4
    assert script.reads == 0
    assert script.now == 4 * PING_AFTER_SECONDS


def test_a_burst_of_changes_is_one_read() -> None:
    script = Script([_entry(S.ANALYSING, 1)], changes_at=(1.0, 1.0, 1.0))

    _collect(script, limit=2)

    assert script.reads == 1


def test_silence_gets_a_ping_within_fifteen_seconds() -> None:
    script = Script()

    (item,) = _collect(script, limit=1)

    assert item == PingEvent()
    assert PING_AFTER_SECONDS <= script.now <= 15


def test_an_event_resets_the_ping_timer() -> None:
    script = Script([_entry(S.ANALYSING, 1)], changes_at=(10.0,))

    items = _collect(script, limit=2)

    assert isinstance(items[0], RunFeedEvent)
    assert items[1] == PingEvent()
    assert script.now == 10.0 + PING_AFTER_SECONDS


def test_an_expired_session_ends_the_stream_without_an_event() -> None:
    script = Script()
    script.session_checks_alive = 0

    items = _collect(script, limit=10)

    assert items == []
    assert script.session_checks == 1
    assert script.now == SESSION_CHECK_SECONDS


def test_the_session_is_rechecked_every_five_seconds_even_in_silence() -> None:
    """Спека `user-auth`: выход закрывает поток не позже чем через 5 секунд — и без сигналов."""
    script = Script()
    script.session_checks_alive = 3

    _collect(script, limit=10)

    assert SESSION_CHECK_SECONDS == 5.0
    assert script.check_times == [5.0, 10.0, 15.0, 20.0]


def test_the_session_is_rechecked_by_the_clock_even_when_reads_are_slow() -> None:
    script = Script(changes_at=tuple(float(t) for t in range(1, 40)))
    script.read_cost = 2.0
    script.session_checks_alive = 3

    items = _collect(script, limit=10)

    # Круг «сигнал + чтение 2 с»: время чтения копится в интервале, поэтому
    # проверки идут по часам, а не по числу сигналов. Проверка делается после
    # ожидания и запаздывает не больше чем на одно чтение.
    assert script.session_checks == 4
    gaps = [b - a for a, b in pairwise([0.0, *script.check_times])]
    limit = SESSION_CHECK_SECONDS + script.read_cost
    assert all(SESSION_CHECK_SECONDS <= gap <= limit for gap in gaps), gaps
    assert all(item == PingEvent() for item in items)


def test_a_lost_source_ends_the_stream() -> None:
    """Источник изменений оборвался — поток кончается: клиент переподключится и возьмёт снимок."""
    script = Script([_entry(S.ANALYSING, 1)], changes_at=(1.0,))
    script.lost_at = 3.0

    items = _collect(script, limit=10)

    assert len(items) == 1
    assert script.now == 3.0


def test_visible_repository_ids_are_the_registered_ones_among_available() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(REPOSITORY)
    available = [
        InstallationRepository("101", "octo-org/api", "main", READ),
        # Доступен у провайдера, но не зарегистрирован: прогонов у него нет.
        InstallationRepository("202", "octo-org/web", "main", READ),
    ]

    assert visible_repository_ids(uow, available) == frozenset({REPOSITORY.id})
    assert visible_repository_ids(uow, []) == frozenset()


def _entries(count: int, seconds: float) -> list[ReviewRunListEntry]:
    """`count` разных прогонов, изменённых в момент `seconds`."""
    base = _entry(S.FAILED, seconds)
    return [replace(base, run=replace(base.run, id=UUID(int=1000 + n))) for n in range(count)]


def test_more_changes_than_one_batch_all_arrive_after_one_signal() -> None:
    """Один коммит меняет больше `BATCH_LIMIT` прогонов (массовый `stale_sweep`) — сигнал один,
    а остаток дочитывается сразу, без ожидания следующего изменения."""
    changed = _entries(BATCH_LIMIT + 50, 1)
    script = Script(changed[:BATCH_LIMIT], changed[BATCH_LIMIT:], changes_at=(1.0,))

    items = _collect(script, limit=BATCH_LIMIT + 50)

    assert len(items) == BATCH_LIMIT + 50
    assert script.reads == 2
    assert script.now == 1.0


def test_a_full_recheck_window_does_not_spin_the_loop() -> None:
    """Под лимит идут только строки за курсором: окно перепроверки из старых версий
    не заставляет перечитывать пачку снова и снова без ожидания."""
    fresh = _entries(BATCH_LIMIT, 1)
    script = Script(fresh, fresh, changes_at=(1.0,))

    _collect(script, limit=BATCH_LIMIT + 1)

    assert script.reads == 2


def test_catching_up_reads_without_the_recheck_window() -> None:
    """Дочитывание без сигнала — без окна перепроверки: иначе пачка с одним `updated_at`
    (массовый `stale_sweep`) перечитывалась бы целиком на каждом круге."""
    changed = _entries(BATCH_LIMIT + 50, 1)
    script = Script(changed[:BATCH_LIMIT], changed[BATCH_LIMIT:], changes_at=(1.0,))

    _collect(script, limit=BATCH_LIMIT + 50)

    _, catch_up = script.recheck_from
    # Без этого правила окно начиналось бы на `RECHECK` (5 с) ниже курсора.
    assert catch_up == script.cursors[1]


def test_a_signal_during_catch_up_reads_with_the_full_window() -> None:
    """Сигнал, пришедший во время дочитывания, — полное окно: им может быть запоздавший
    коммит с `updated_at` ниже курсора, и без окна он не придёт никогда."""
    changed = _entries(BATCH_LIMIT + 50, 1)
    script = Script(changed[:BATCH_LIMIT], changed[BATCH_LIMIT:], changes_at=(1.0, 1.2))
    script.read_cost = 0.5

    _collect(script, limit=BATCH_LIMIT + 50)

    assert script.recheck_from[1] == script.cursors[1] - RECHECK
