"""Цикл общего потока событий: опрос изменённых прогонов, сессия, ping.

Источник — опрос базы раз в `POLL_SECONDS`: статус пишет воркер, общего у API и
воркера только база. Какие события отдать, решает `advance_feed`; чтение пачки,
ожидание, проверка сессии и часы приходят аргументами, поэтому цикл не знает ни
про базу, ни про event loop, и тест прокручивает минуту тишины мгновенно.

Позже источником станет PostgreSQL NOTIFY; события и их данные при этом не
меняются.
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable
from uuid import UUID

from app.application.ports import UnitOfWork
from app.domain.entities import InstallationRepository, ReviewRunListEntry
from app.domain.enums import Provider
from app.domain.run_events import PING_AFTER_SECONDS, PingEvent
from app.domain.run_feed import RunFeedEvent, RunFeedState, advance_feed

# Спека обещает событие не позже двух секунд после записи статуса.
POLL_SECONDS = 1.0
# Спека `user-auth`: сессия перепроверяется каждые 5 секунд по монотонным часам,
# поэтому поток закрывается не позже чем через 5 секунд и один круг опроса после выхода.
SESSION_CHECK_SECONDS = 5.0

type FeedItem = RunFeedEvent | PingEvent


def visible_repository_ids(uow: UnitOfWork, repositories: Iterable[InstallationRepository]) -> frozenset[UUID]:
    """Зарегистрированные репозитории из списка доступных пользователю.

    Список — ответ `list_user_repositories`, того же запроса к GitHub, что у
    GET /repositories/available: правило видимости одно и живёт там. Здесь только
    сопоставление с зарегистрированными по провайдеру и id у провайдера — одним запросом.
    """
    provider_ids = [repository.provider_id for repository in repositories]
    with uow:
        return frozenset(r.id for r in uow.repositories.list_by_provider_ids(Provider.GITHUB, provider_ids))


async def run_feed_events(
    state: RunFeedState,
    read_batch: Callable[[RunFeedState], Awaitable[list[ReviewRunListEntry]]],
    *,
    sleep: Callable[[float], Awaitable[None]],
    session_alive: Callable[[], Awaitable[bool]],
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[FeedItem]:
    """Отдавать события прогонов, пока жива сессия.

    `state` — состояние после первого опроса (`start_feed`): поток не начинается
    с истории. Дальше раз в `POLL_SECONDS` — одно чтение пачки. Сессия
    перечитывается раз в `SESSION_CHECK_SECONDS` по монотонным часам: нет её —
    поток кончается без события. `ping` — если с последнего отправленного события
    прошло `PING_AFTER_SECONDS`, тоже по часам: чтение пачки занимает время.
    """
    last_session_check = clock()
    last_sent = clock()
    while True:
        await sleep(POLL_SECONDS)
        if clock() - last_session_check >= SESSION_CHECK_SECONDS:
            last_session_check = clock()
            if not await session_alive():
                return
        state, events = advance_feed(state, await read_batch(state))
        for event in events:
            yield event
        if events:
            last_sent = clock()
        elif clock() - last_sent >= PING_AFTER_SECONDS:
            yield PingEvent()
            last_sent = clock()
