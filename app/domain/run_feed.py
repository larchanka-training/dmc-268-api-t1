"""Общий поток событий: какие события прогонов отдать клиенту после очередного опроса.

Функция чистая: состояние соединения и пачка изменённых прогонов приходят
аргументами, новое состояние и события — результатом. Цикл опроса живёт в
`app/application/run_feed_events.py`, запись в текст SSE — в `app/api/events.py`.

Источник — опрос базы по курсору `(updated_at, id)`. `updated_at` записи
выставляется раньше коммита (время транзакции в базе или часы процесса), поэтому
запись, закоммиченная позже соседней, может прийти с меньшим `updated_at`, чем уже
увиденный курсор. Чтобы её не потерять, каждый опрос заодно перечитывает окно
`RECHECK` под курсором, а уже отправленные версии отсеивает по `recent`.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import UUID

from app.domain.entities import ReviewRunListEntry
from app.domain.enums import TERMINAL_STATUSES

# Окно перепроверки под курсором: запись, закоммиченная на столько позже своего
# `updated_at`, ещё доходит. Транзакции записи прогона длятся миллисекунды; запас —
# на расхождение часов процессов, которые пишут `updated_at`.
RECHECK = timedelta(seconds=5)
# Сколько прогонов читает один опрос; остальное — следующим.
BATCH_LIMIT = 200


class RunEventType(StrEnum):
    """Имя события прогона в поле `event:`."""

    CREATED = "run.created"
    STATUS_CHANGED = "run.status_changed"
    FINISHED = "run.finished"


@dataclass(frozen=True, slots=True, order=True)
class FeedCursor:
    """Самая новая увиденная версия: порядок — по `updated_at`, при равном — по `id`."""

    updated_at: datetime
    id: UUID


@dataclass(frozen=True, slots=True)
class RunFeedEvent:
    type: RunEventType
    entry: ReviewRunListEntry


@dataclass(frozen=True, slots=True)
class RunFeedState:
    """Что поток помнит о соединении.

    `floor` — нижняя граница всего, что поток вообще отдаёт: раньше неё — история
    до подключения. `recent` — отправленные (или учтённые при подключении) версии
    прогонов из окна перепроверки. `announced` — прогоны, созданные после
    подключения и уже показанные: `run.created` у прогона один.
    """

    connected_at: datetime
    floor: datetime
    cursor: FeedCursor
    recent: Mapping[UUID, datetime]
    announced: frozenset[UUID]

    @property
    def recheck_from(self) -> datetime:
        """Нижняя граница перепроверки (исключительно): окно под курсором, но не раньше `floor`."""
        return max(self.cursor.updated_at - RECHECK, self.floor)


def open_feed(connected_at: datetime) -> RunFeedState:
    """Состояние до первого опроса: курсор — на `RECHECK` раньше подключения.

    Первый опрос (`start_feed`) учитывает прогоны этого окна как уже виденные:
    их последние версии клиент получит снимком REST, событий по ним не будет.
    """
    floor = connected_at - RECHECK
    return RunFeedState(
        connected_at=connected_at,
        floor=floor,
        cursor=FeedCursor(floor, UUID(int=0)),
        recent={},
        announced=frozenset(),
    )


def start_feed(state: RunFeedState, baseline: Iterable[ReviewRunListEntry]) -> RunFeedState:
    """Учесть пачку первого опроса без событий: истории поток не отдаёт."""
    started, _ = advance_feed(state, baseline)
    return started


def advance_feed(
    state: RunFeedState, batch: Iterable[ReviewRunListEntry]
) -> tuple[RunFeedState, list[RunFeedEvent]]:
    """Новое состояние и события по пачке опроса — в порядке `(updated_at, id)`.

    Версия, которая уже есть в `recent`, события не даёт; любая другая — даёт одно.
    """
    cursor = state.cursor
    recent = dict(state.recent)
    announced = set(state.announced)
    events: list[RunFeedEvent] = []
    for entry in sorted(batch, key=lambda e: (e.run.updated_at, e.run.id)):
        run = entry.run
        cursor = max(cursor, FeedCursor(run.updated_at, run.id))
        if recent.get(run.id) == run.updated_at:
            continue
        recent[run.id] = run.updated_at
        created_after_connect = run.created_at > state.connected_at
        events.append(RunFeedEvent(_event_type(entry, created_after_connect, announced), entry))
        if created_after_connect:
            announced.add(run.id)
    advanced = replace(state, cursor=cursor, announced=frozenset(announced))
    # Версия не выше окна перепроверки опросом больше не вернётся — помнить её незачем.
    lower = advanced.recheck_from
    return replace(advanced, recent={i: at for i, at in recent.items() if at > lower}), events


def _event_type(
    entry: ReviewRunListEntry, created_after_connect: bool, announced: set[UUID]
) -> RunEventType:
    """Итоговый статус — всегда `run.finished`, даже у прогона, впервые увиденного
    уже завершённым: при переходе в итоговый статус приходит только он."""
    if entry.run.status in TERMINAL_STATUSES:
        return RunEventType.FINISHED
    if created_after_connect and entry.run.id not in announced:
        return RunEventType.CREATED
    return RunEventType.STATUS_CHANGED
