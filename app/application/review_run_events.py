"""Цикл потока событий статуса одного прогона.

API и воркер — разные процессы, и общего у них только база: статус пишет
воркер, а этот цикл раз в секунду читает его и отдаёт клиенту то, что решит
`next_event`. Чтение и ожидание приходят аргументами: цикл не знает ни про
базу, ни про event loop, и тест прокручивает минуту тишины мгновенно.
"""

import time
from collections.abc import AsyncIterator, Awaitable, Callable

from app.domain.entities import ReviewRun
from app.domain.enums import ReviewRunStatus
from app.domain.run_events import RunEvent, StatusEvent, next_event

# Спека обещает событие не позже двух секунд после записи статуса.
POLL_SECONDS = 1.0
# Спека `user-auth`: сессия перепроверяется каждые 5 секунд по монотонным часам,
# поэтому поток закрывается не позже чем через 5 секунд и один круг опроса после выхода.
SESSION_CHECK_SECONDS = 5.0


async def review_run_events(
    run: ReviewRun,
    read_run: Callable[[], Awaitable[ReviewRun | None]],
    *,
    sleep: Callable[[float], Awaitable[None]],
    session_alive: Callable[[], Awaitable[bool]],
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[RunEvent]:
    """Отдавать события прогона, пока он не завершится.

    `run` — уже прочитанный прогон: из него первым уходит снимок, и поток
    никогда не открывается пустым. Дальше прогон перечитывается раз в
    `POLL_SECONDS`. Поток кончается после терминального статуса или если
    прогон перестал читаться.

    Сессию проверили при подключении, но поток живёт минуты, поэтому раз в
    `SESSION_CHECK_SECONDS` она перечитывается: нет её — поток закрывается
    без события. Аргумент обязательный, чтобы поток без проверки не открылся.
    Срок отсчитывается по `clock` (монотонным часам), а не суммой ожиданий:
    чтение прогона и отдача события тоже занимают время.
    """
    last_sent: ReviewRunStatus | None = None
    idle_seconds = 0.0
    last_session_check = clock()
    while True:
        decision = next_event(run, last_sent=last_sent, idle_seconds=idle_seconds)
        if decision.event is not None:
            yield decision.event
            idle_seconds = 0.0
            if isinstance(decision.event, StatusEvent):
                last_sent = decision.event.status
        if decision.close:
            return
        await sleep(POLL_SECONDS)
        idle_seconds += POLL_SECONDS
        if clock() - last_session_check >= SESSION_CHECK_SECONDS:
            last_session_check = clock()
            if not await session_alive():
                return
        next_run = await read_run()
        if next_run is None:
            return
        run = next_run
