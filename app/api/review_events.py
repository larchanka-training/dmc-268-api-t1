"""Поток событий статуса прогона по Server-Sent Events.

Роутер только переводит: `404` до начала потока, события — в текст SSE,
заголовки — против кэша и буфера прокси. Что и когда отправлять, решают
`next_event` и цикл `review_run_events`. Сессия проверяется при подключении
зависимостью роутера и дальше — в цикле, по той же cookie.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.dependencies import (
    get_clock,
    get_run_reader,
    get_sleep,
    get_unit_of_work_factory,
)
from app.api.errors import ERRORS_404_REVIEW, ERRORS_422, ApiError
from app.api.problems import ErrorCode
from app.api.schemas import FAILED_STAGE_DESCRIPTION, FailedStage, UtcDatetime
from app.api.session import SESSION_COOKIE, SESSION_REQUIRED, SESSION_RESPONSES
from app.application.ports import UnitOfWork
from app.application.review_run_events import review_run_events
from app.application.use_cases.auth import authenticate
from app.domain.entities import ReviewRun
from app.domain.enums import ReviewRunStatus
from app.domain.run_events import PingEvent, RunEvent

router = APIRouter(
    prefix="/api/v1/reviews",
    tags=["reviews"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)

# Через сколько миллисекунд `EventSource` переподключается после обрыва.
_RETRY = "retry: 3000\n\n"

_HEADERS = {
    "Cache-Control": "no-cache",
    # nginx не копит события в буфере — иначе они дошли бы пачкой.
    "X-Accel-Buffering": "no",
}


class ReviewRunStatusEvent(BaseModel):
    """Данные события `status` потока: `event: status`, `data: <JSON в одну строку>`.

    Событие `ping` приходит с `data: {}`. Тело ответа — текст SSE, а не этот JSON; в схему
    модель попадает отдельным компонентом (`app/api/openapi.py`, `extra_models`).
    """

    review_run_id: UUID = Field(description="Прогон, о котором событие.")
    status: ReviewRunStatus = Field(description="Статус прогона на момент события.", examples=["analysing"])
    last_progress_at: UtcDatetime = Field(description="Время последнего перехода состояния.")
    failed_stage: FailedStage | None = Field(description=FAILED_STAGE_DESCRIPTION)
    failure_reason: str | None = Field(description="Почему прогон упал; null, если не падал.")


def format_sse(event: RunEvent) -> str:
    """Записать событие в формате Server-Sent Events.

    Именованное событие, а не комментарий: комментарии `EventSource` до кода клиента не
    доводит. Данные `status` — JSON модели `ReviewRunStatusEvent` в одну строку: перенос
    внутри `data` разбил бы событие на несколько полей, а JSON переносы экранирует.
    """
    if isinstance(event, PingEvent):
        return "event: ping\ndata: {}\n\n"
    data = ReviewRunStatusEvent.model_validate(event, from_attributes=True).model_dump_json()
    return f"event: status\ndata: {data}\n\n"


_STREAM_DOC: dict[int | str, dict[str, Any]] = {
    HTTPStatus.OK: {
        "description": "Поток событий; данные события `status` — ReviewRunStatusEvent.",
        "content": {
            "text/event-stream": {
                "schema": {
                    "type": "string",
                    "description": "Текст SSE: `event: status` с JSON ReviewRunStatusEvent в `data` или `event: ping`.",
                }
            }
        },
        "headers": {
            "Cache-Control": {"schema": {"type": "string", "const": "no-cache"}},
            "X-Accel-Buffering": {
                "description": "Отключает буферизацию ответа в nginx.",
                "schema": {"type": "string", "const": "no"},
            },
        },
    },
}


@router.get(
    "/{review_run_id}/events",
    response_class=StreamingResponse,
    summary="Поток событий статуса прогона (Server-Sent Events)",
    description=(
        "Первое событие после подключения — `status` со снимком текущего состояния, дальше — "
        "по событию `status` на каждую смену статуса и `ping` в тишине, не реже раза в 15 "
        "секунд. После терминального статуса сервер закрывает поток. Поток начинается со "
        "строки `retry: 3000`. Поток отражает состояние, а не журнал: несколько переходов "
        "между проверками приходят одним событием. Требует сессию: она проверяется при "
        "подключении и затем каждые 5 секунд, по её окончании поток закрывается."
    ),
    responses={**_STREAM_DOC, **ERRORS_404_REVIEW, **ERRORS_422},
)
async def stream_review_events(
    review_run_id: Annotated[UUID, Path(description="Идентификатор прогона ревью.")],
    request: Request,
    read_run: Callable[[UUID], ReviewRun | None] = Depends(get_run_reader),
    sleep: Callable[[float], Awaitable[None]] = Depends(get_sleep),
    clock: Callable[[], float] = Depends(get_clock),
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
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.REVIEW_NOT_FOUND, "Прогона ревью с таким id нет.")

    async def body() -> AsyncIterator[str]:
        yield _RETRY
        async for event in review_run_events(
            run, read, sleep=sleep, session_alive=session_alive, clock=clock
        ):
            yield format_sse(event)

    return StreamingResponse(body(), media_type="text/event-stream", headers=_HEADERS)
