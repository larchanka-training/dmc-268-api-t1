"""Поток событий статуса прогона по Server-Sent Events.

Роутер только переводит: `404` до начала потока, события — в текст SSE,
заголовки — против кэша и буфера прокси. Что и когда отправлять, решают
`next_event` и цикл `review_run_events`. Сессия проверяется при подключении
зависимостью роутера и дальше — в цикле, по той же cookie.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_run_reader, get_sleep, get_unit_of_work_factory
from app.api.session import SESSION_COOKIE, SESSION_REQUIRED
from app.application.ports import UnitOfWork
from app.application.review_run_events import review_run_events
from app.application.use_cases.auth import authenticate
from app.domain.entities import ReviewRun
from app.domain.run_events import format_sse

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"], dependencies=SESSION_REQUIRED)

# Через сколько миллисекунд `EventSource` переподключается после обрыва.
_RETRY = "retry: 3000\n\n"

_HEADERS = {
    "Cache-Control": "no-cache",
    # nginx не копит события в буфере — иначе они дошли бы пачкой.
    "X-Accel-Buffering": "no",
}


@router.get(
    "/{review_run_id}/events",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"text/event-stream": {}}, "description": "Поток событий прогона"},
        401: {"description": "Нет действительной сессии"},
        404: {"description": "Прогон не найден"},
    },
)
async def stream_review_events(
    review_run_id: UUID,
    request: Request,
    read_run: Callable[[UUID], ReviewRun | None] = Depends(get_run_reader),
    sleep: Callable[[float], Awaitable[None]] = Depends(get_sleep),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> StreamingResponse:
    token = request.cookies.get(SESSION_COOKIE)

    async def session_alive() -> bool:
        user = await run_in_threadpool(authenticate, uow_factory(), token, datetime.now(UTC))
        return user is not None

    # Чтение синхронное, обработчик — нет: поток живёт минуты и не должен
    # занимать ни поток пула, ни event loop на время запроса к базе.
    async def read() -> ReviewRun | None:
        return await run_in_threadpool(read_run, review_run_id)

    # До начала потока: после первого байта статус ответа уже не поменять.
    # Прочитанный прогон становится снимком — второго чтения нет, и прогон,
    # удалённый между проверкой и потоком, не даёт пустой поток.
    run = await read()
    if run is None:
        raise HTTPException(status_code=404, detail="review run not found")

    async def body() -> AsyncIterator[str]:
        yield _RETRY
        async for event in review_run_events(run, read, sleep=sleep, session_alive=session_alive):
            yield format_sse(event)

    return StreamingResponse(body(), media_type="text/event-stream", headers=_HEADERS)
