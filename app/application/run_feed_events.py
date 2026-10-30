"""Цикл общего потока событий: чтение изменённых прогонов по сигналу, сессия, ping.

Источник — сигнал «прогоны изменились» (`RunChangeSubscription`, PostgreSQL
`NOTIFY` при коммите записи прогона): база читается только после него, а не раз в
секунду на каждое соединение. Какие события отдать, решает `advance_feed`;
ожидание сигнала, чтение пачки, проверка сессии и часы приходят аргументами,
поэтому цикл не знает ни про базу, ни про event loop, и тест прокручивает минуту
тишины мгновенно.
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from dataclasses import replace
from uuid import UUID

from app.application.ports import UnitOfWork
from app.application.ports.run_changes import RunChangesLost
from app.domain.entities import InstallationRepository, ReviewRunListEntry
from app.domain.enums import Provider
from app.domain.run_events import PING_AFTER_SECONDS, PingEvent
from app.domain.run_feed import (
    BATCH_LIMIT,
    FeedCursor,
    RunFeedEvent,
    RunFeedState,
    advance_feed,
)

# Спека `user-auth`: сессия перепроверяется каждые 5 секунд по монотонным часам,
# поэтому поток закрывается не позже чем через 5 секунд после выхода.
SESSION_CHECK_SECONDS = 5.0

type FeedItem = RunFeedEvent | PingEvent


def visible_repository_ids(uow: UnitOfWork, repositories: Iterable[InstallationRepository]) -> frozenset[UUID]:
    """Зарегистрированные репозитории из списка видимых пользователю.

    Список — ответ `list_user_repositories`, того же запроса к GitHub, что у
    GET /repositories: правило видимости одно и живёт там. Здесь только
    сопоставление с зарегистрированными по провайдеру и id у провайдера — одним запросом.
    """
    provider_ids = [repository.provider_id for repository in repositories]
    with uow:
        return frozenset(r.id for r in uow.repositories.list_by_provider_ids(Provider.GITHUB, provider_ids))


async def run_feed_events(
    state: RunFeedState,
    read_batch: Callable[[RunFeedState], Awaitable[list[ReviewRunListEntry]]],
    *,
    wait_change: Callable[[float], Awaitable[bool]],
    session_alive: Callable[[], Awaitable[bool]],
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[FeedItem]:
    """Отдавать события прогонов, пока жива сессия и источник изменений.

    `state` — состояние после первого чтения (`start_feed`): поток не начинается
    с истории. Дальше — ожидание сигнала об изменениях, не дольше, чем до
    ближайшей проверки сессии или `ping`; сигнал — одно чтение пачки. Сессия
    перечитывается раз в `SESSION_CHECK_SECONDS` по монотонным часам: нет её —
    поток кончается без события. `ping` — если с последнего отправленного события
    прошло `PING_AFTER_SECONDS`. Источник оборвался (`RunChangesLost`) — поток
    кончается: изменения за время обрыва могли потеряться, клиент переподключится
    и возьмёт снимок.
    """
    last_session_check = clock()
    last_sent = clock()
    more = False
    while True:
        due = min(last_session_check + SESSION_CHECK_SECONDS, last_sent + PING_AFTER_SECONDS)
        try:
            # Прошлое чтение упёрлось в `BATCH_LIMIT` — остаток читается сразу:
            # один сигнал на всю пачку, второго может не быть (массовый `stale_sweep`).
            signalled = await wait_change(0.0 if more else max(due - clock(), 0.0))
        except RunChangesLost:
            return
        if clock() - last_session_check >= SESSION_CHECK_SECONDS:
            last_session_check = clock()
            if not await session_alive():
                return
        changed, catching_up, more = signalled or more, more and not signalled, False
        if changed:
            # Дочитывание без нового сигнала — без окна перепроверки: запоздавший коммит
            # пришлёт свой NOTIFY, а окно с одним `updated_at` на всю пачку (`stale_sweep`)
            # перечитывалось бы целиком на каждом круге.
            read_state = replace(state, floor=state.cursor.updated_at) if catching_up else state
            batch = await read_batch(read_state)
            # Лимит — у строк за курсором; окно перепроверки под ним в счёт не идёт.
            more = sum(FeedCursor(e.run.updated_at, e.run.id) > state.cursor for e in batch) >= BATCH_LIMIT
            state, events = advance_feed(state, batch)
            for event in events:
                yield event
            if events:
                last_sent = clock()
        if clock() - last_sent >= PING_AFTER_SECONDS:
            yield PingEvent()
            last_sent = clock()
