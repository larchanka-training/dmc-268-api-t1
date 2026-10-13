"""Поток событий статуса прогона по Server-Sent Events.

Роутер только переводит: `404` до начала потока, события — в текст SSE,
заголовки — против кэша и буфера прокси. Что и когда отправлять, решают
`next_event` и цикл `review_run_events`.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_run_reader, get_sleep
from app.application.review_run_events import review_run_events
from app.domain.entities import ReviewRun
from app.domain.run_events import format_sse

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])

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
        404: {"description": "Прогон не найден"},
    },
)
async def stream_review_events(
    review_run_id: UUID,
    read_run: Callable[[UUID], ReviewRun | None] = Depends(get_run_reader),
    sleep: Callable[[float], Awaitable[None]] = Depends(get_sleep),
) -> StreamingResponse:
    # Чтение синхронное, обработчик — нет: поток живёт минуты и не должен
    # занимать ни поток пула, ни event loop на время запроса к базе.
    async def read() -> ReviewRun | None:
        return await run_in_threadpool(read_run, review_run_id)

    # До начала потока: после первого байта статус ответа уже не поменять.
    if await read() is None:
        raise HTTPException(status_code=404, detail="review run not found")

    async def body() -> AsyncIterator[str]:
        yield _RETRY
        async for event in review_run_events(read, sleep=sleep):
            yield format_sse(event)

    return StreamingResponse(body(), media_type="text/event-stream", headers=_HEADERS)
