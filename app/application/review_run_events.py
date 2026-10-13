"""Цикл потока событий статуса одного прогона.

API и воркер — разные процессы, и общего у них только база: статус пишет
воркер, а этот цикл раз в секунду читает его и отдаёт клиенту то, что решит
`next_event`. Чтение и ожидание приходят аргументами: цикл не знает ни про
базу, ни про event loop, и тест прокручивает минуту тишины мгновенно.
"""

from collections.abc import AsyncIterator, Awaitable, Callable

from app.domain.entities import ReviewRun
from app.domain.enums import ReviewRunStatus
from app.domain.run_events import RunEvent, StatusEvent, next_event

# Спека обещает событие не позже двух секунд после записи статуса.
POLL_SECONDS = 1.0


async def review_run_events(
    read_run: Callable[[], Awaitable[ReviewRun | None]],
    *,
    sleep: Callable[[float], Awaitable[None]],
) -> AsyncIterator[RunEvent]:
    """Отдавать события прогона, пока он не завершится.

    Первым всегда уходит снимок. Поток кончается после терминального статуса
    или если прогон перестал читаться.
    """
    last_sent: ReviewRunStatus | None = None
    idle_seconds = 0.0
    while True:
        run = await read_run()
        if run is None:
            return
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
