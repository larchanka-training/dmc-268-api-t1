"""Цикл общего потока событий — поддельные чтение, ожидание и часы, без базы и настоящего сна."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.review_run_events import POLL_SECONDS, SESSION_CHECK_SECONDS
from app.application.run_feed_events import (
    FeedItem,
    run_feed_events,
    visible_repository_ids,
)
from app.application.use_cases.connect_repository import AvailableRepository
from app.domain.entities import (
    InstallationRepository,
    MergeRequest,
    Repository,
    ReviewRun,
    ReviewRunListEntry,
)
from app.domain.enums import MergeRequestState, Provider, TriggerSource
from app.domain.enums import ReviewRunStatus as S
from app.domain.run_events import PING_AFTER_SECONDS, PingEvent
from app.domain.run_feed import RunEventType, RunFeedEvent, RunFeedState, open_feed

from ..fakes.unit_of_work import FakeUnitOfWork

CONNECTED = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)
REPOSITORY = Repository(
    id=UUID(int=1),
    provider=Provider.GITHUB,
    provider_id="101",
    full_name="octo-org/api",
    default_branch="main",
    auto_review_enabled=True,
    created_at=CONNECTED,
    updated_at=CONNECTED,
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
    """Пачки, которые вернут следующие опросы; дальше — пусто. Часы идут ожиданием."""

    def __init__(self, *batches: list[ReviewRunListEntry]) -> None:
        self._batches = list(batches)
        self.reads = 0
        self.now = 0.0
        self.session_checks = 0
        self.session_checks_alive: int | None = None

    async def read(self, state: RunFeedState) -> list[ReviewRunListEntry]:
        self.reads += 1
        return self._batches.pop(0) if self._batches else []

    async def sleep(self, seconds: float) -> None:
        self.now += seconds

    def clock(self) -> float:
        return self.now

    async def session_alive(self) -> bool:
        self.session_checks += 1
        return self.session_checks_alive is None or self.session_checks <= self.session_checks_alive


def _collect(script: Script, *, limit: int) -> list[FeedItem]:
    async def run() -> list[FeedItem]:
        items: list[FeedItem] = []
        async for item in run_feed_events(
            open_feed(CONNECTED),
            script.read,
            sleep=script.sleep,
            session_alive=script.session_alive,
            clock=script.clock,
        ):
            items.append(item)
            if len(items) >= limit:
                break
        return items

    return asyncio.run(run())


def test_each_poll_reads_one_batch_and_turns_it_into_events() -> None:
    analysing = _entry(S.ANALYSING, 1)
    completed = _entry(S.COMPLETED, 2)
    script = Script([analysing], [completed])

    items = _collect(script, limit=2)

    assert items == [
        RunFeedEvent(RunEventType.STATUS_CHANGED, analysing),
        RunFeedEvent(RunEventType.FINISHED, completed),
    ]
    assert script.reads == 2
    assert script.now == 2 * POLL_SECONDS


def test_silence_gets_a_ping_within_fifteen_seconds() -> None:
    script = Script()

    (item,) = _collect(script, limit=1)

    assert item == PingEvent()
    assert PING_AFTER_SECONDS <= script.now <= 15


def test_an_event_resets_the_ping_timer() -> None:
    script = Script(*([[]] * 9), [_entry(S.ANALYSING, 1)])

    items = _collect(script, limit=2)

    assert isinstance(items[0], RunFeedEvent)
    assert items[1] == PingEvent()
    assert script.now - 10 * POLL_SECONDS >= PING_AFTER_SECONDS


def test_an_expired_session_ends_the_stream_without_an_event() -> None:
    script = Script()
    script.session_checks_alive = 0

    items = _collect(script, limit=10)

    assert items == []
    assert script.session_checks == 1
    assert script.now == SESSION_CHECK_SECONDS


def test_visible_repository_ids_are_the_registered_ones_among_available() -> None:
    uow = FakeUnitOfWork()
    uow.repositories.add(REPOSITORY)
    available = [
        AvailableRepository(
            InstallationRepository("101", "octo-org/api", "main", installation_id=7, private=True),
            already_connected=True,
        ),
        # Доступен у провайдера, но не зарегистрирован: прогонов у него нет.
        AvailableRepository(
            InstallationRepository("202", "octo-org/web", "main", installation_id=7, private=True),
            already_connected=False,
        ),
    ]

    assert visible_repository_ids(uow, available) == frozenset({REPOSITORY.id})
    assert visible_repository_ids(uow, []) == frozenset()
