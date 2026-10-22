"""Поток событий кабинета по Server-Sent Events.

Один поток на всё: прогоны сейчас, другие ресурсы потом. Имя события —
`<ресурс>.<действие>`, данные — модель своего типа (`x-sse-events`); у `run.*` —
`ReviewRunListItem` из `app/api/reviews.py`.

Роутер переводит: набор видимых репозиториев и первый опрос — до ответа (сбой
провайдера — `502` до начала потока), события — в текст SSE. Что отправлять,
решают `advance_feed` и цикл `run_feed_events`.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import RootModel
from starlette.types import Message, Receive, Scope, Send

from app.api.dependencies import (
    get_clock,
    get_sleep,
    get_unit_of_work_factory,
    get_untraced_unit_of_work_factory,
)
from app.api.errors import ERRORS_502_SCM_EVENTS
from app.api.repositories import available_repositories
from app.api.reviews import VISIBILITY_RULE, review_run_list_item
from app.api.schemas import STREAM_HEADERS_DOC
from app.api.session import SESSION_COOKIE, SESSION_REQUIRED, SESSION_RESPONSES
from app.application.ports import UnitOfWork
from app.application.run_feed_events import (
    FeedItem,
    run_feed_events,
    visible_repository_ids,
)
from app.application.use_cases.auth import authenticate
from app.application.use_cases.connect_repository import AvailableRepository
from app.domain.entities import ReviewRunListEntry
from app.domain.run_events import PingEvent
from app.domain.run_feed import BATCH_LIMIT, RunFeedState, open_feed, start_feed

_STREAM_DESCRIPTION = (
    "Требует сессию. Один поток Server-Sent Events на весь кабинет, без параметров: сейчас для "
    "прогонов ревью, дальше и для других ресурсов. Заменит поток одного прогона "
    "GET /reviews/{review_run_id}/events. Имя события — `<ресурс>.<действие>` в поле `event:`, "
    "значения — StreamEventType; данные — JSON модели своего типа в `data:` одной строкой, какая "
    "модель у какого типа — в `x-sse-events` ответа. Сейчас типы такие: `run.created` — прогон, "
    "созданный после подключения, при первом его событии в этом соединении; `run.status_changed` — "
    "прогон изменился, но не в итоговом статусе (в том числе только прогресс без смены статуса — "
    "клиент всё равно делает upsert по id); `run.finished` — прогон в итоговом статусе (completed, "
    "failed или cancelled), при переходе в итоговый статус приходит только `run.finished`, даже у "
    "прогона, созданного после подключения. Данные `run.*` — ReviewRunListItem, полная строка списка "
    "GET /reviews. `ping` с `data: {}` — в тишине, не реже раза в 15 секунд. Клиент игнорирует "
    "незнакомые типы событий: новые типы добавляются без правки клиентов. Фильтров у потока нет: "
    "клиент получает все видимые ему события и отбирает нужные сам. Видимость задаёт правило типа "
    "события, пользователь получает только видимые ему события. Для `run.*`: " + VISIBILITY_RULE + " "
    "Набор видимых репозиториев считается при подключении тем же расчётом, что ответ "
    "GET /repositories/available, и до переподключения не пересчитывается: доступ, отобранный у "
    "провайдера, учитывается при переподключении. Если GitHub не ответил или ответил ошибкой — "
    "`502 SCM_UNAVAILABLE`, поток не открывается. Клиент применяет любое событие прогона одинаково: "
    "upsert по id, из двух версий побеждает та, у которой updated_at больше, при равном — пришедшая "
    "позже. Источник событий — опрос базы раз в секунду: событие приходит не позже чем через пару "
    "секунд после записи прогона, несколько изменений прогона между опросами приходят одним событием "
    "с последней версией. Позже источником станет PostgreSQL NOTIFY, контракт при этом не меняется. "
    "Истории поток не отдаёт: при подключении и после каждого переподключения клиент перезапрашивает "
    "снимок REST (GET /reviews/{review_run_id} или GET /reviews) после события `open` у "
    "EventSource; событие, пришедшее раньше снимка, не потеряется, а более старая версия строки "
    "проиграет по updated_at. Поток начинается со строки `retry: 3000`. Сессия проверяется при "
    "подключении и затем каждые 5 секунд, по её окончании поток закрывается."
)


class StreamEventType(RootModel[Literal["run.created", "run.status_changed", "run.finished", "ping"]]):
    """Имя события потока GET /events — значение поля `event:` в тексте SSE, `<ресурс>.<действие>`.

    У `run.*` данные — ReviewRunListItem, у `ping` — `{}`. Список растёт: клиент игнорирует
    незнакомые имена. Тело ответа — текст SSE, поэтому в схему модель попадает отдельным
    компонентом (`app/api/openapi.py`, `extra_models`).
    """


_RUN_ROW = {"$ref": "#/components/schemas/ReviewRunListItem"}

_STREAM_DOC: dict[int | str, dict[str, Any]] = {
    HTTPStatus.OK: {
        "description": "Поток событий; данные события — модель его типа, перечислены в x-sse-events.",
        "content": {
            "text/event-stream": {
                "schema": {
                    "type": "string",
                    "description": (
                        "Текст SSE: `event: <ресурс>.<действие>` с JSON модели типа в `data`; сейчас "
                        "`run.created`, `run.status_changed` и `run.finished` с ReviewRunListItem или "
                        "`event: ping` с `data: {}`. Незнакомые типы клиент игнорирует."
                    ),
                },
                # Какие данные у какого события — ссылками на модели, а не только текстом.
                "x-sse-events": {
                    "run.created": _RUN_ROW,
                    "run.status_changed": _RUN_ROW,
                    "run.finished": _RUN_ROW,
                    "ping": {"type": "object", "maxProperties": 0},
                },
            }
        },
        "headers": STREAM_HEADERS_DOC,
    },
}

# Через сколько миллисекунд `EventSource` переподключается после обрыва.
_RETRY = "retry: 3000\n\n"
_HEADERS = {
    "Cache-Control": "no-cache",
    # nginx не копит события в буфере — иначе они дошли бы пачкой.
    "X-Accel-Buffering": "no",
}

# Сессия — на весь роутер, как у остальных ручек `/api/v1`.
router = APIRouter(
    prefix="/api/v1/events",
    tags=["events"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


def format_feed_event(item: FeedItem) -> str:
    """Записать событие в формате Server-Sent Events: `event:` и JSON в `data:` одной строкой.

    JSON экранирует переводы строк, поэтому событие не разобьётся на несколько полей `data`.
    """
    if isinstance(item, PingEvent):
        return "event: ping\ndata: {}\n\n"
    data = review_run_list_item(item.entry).model_dump_json()
    return f"event: {item.type}\ndata: {data}\n\n"


class _EventStream(StreamingResponse):
    """Поток, который завершает ответ и тогда, когда клиент ушёл сам.

    На `http.disconnect` Starlette отменяет генератор, и ответ остаётся без последнего
    сообщения — телеметрия FastAPI считает такой запрос сбоем (ERROR на спане запроса).
    Для потока уход клиента — штатный конец, поэтому после отмены ответ закрывается
    пустым телом: сервер, от которого клиент уже отключился, его просто отбросит.
    Исключение из генератора по-прежнему остаётся сбоем.
    """

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        completed = False

        async def tracked(message: Message) -> None:
            nonlocal completed
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                completed = True
            await send(message)

        await super().__call__(scope, receive, tracked)
        if not completed:
            await send({"type": "http.response.body", "body": b"", "more_body": False})


@router.get(
    "",
    response_class=StreamingResponse,
    summary="Поток событий кабинета (Server-Sent Events)",
    description=_STREAM_DESCRIPTION,
    responses={**_STREAM_DOC, **ERRORS_502_SCM_EVENTS},
)
async def stream_events(
    request: Request,
    available: list[AvailableRepository] = Depends(available_repositories),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
    untraced_uow_factory: Callable[[], UnitOfWork] = Depends(get_untraced_unit_of_work_factory),
    sleep: Callable[[float], Awaitable[None]] = Depends(get_sleep),
    clock: Callable[[], float] = Depends(get_clock),
) -> StreamingResponse:
    token = request.cookies.get(SESSION_COOKIE)
    connected_at = datetime.now(UTC)
    # Набор видимых — один раз, до потока: сбой провайдера уже стал 502 в зависимости.
    visible = await run_in_threadpool(visible_repository_ids, uow_factory(), available)

    def read_sync(state: RunFeedState) -> list[ReviewRunListEntry]:
        with untraced_uow_factory() as uow:
            return uow.review_runs.list_changed(state.cursor, state.recheck_from, visible, BATCH_LIMIT)

    # Чтение синхронное, обработчик — нет: поток живёт часами и не должен занимать
    # event loop на время запроса к базе.
    async def read(state: RunFeedState) -> list[ReviewRunListEntry]:
        return await run_in_threadpool(read_sync, state)

    async def session_alive() -> bool:
        signed_in = await run_in_threadpool(authenticate, untraced_uow_factory(), token, datetime.now(UTC))
        return signed_in is not None

    # Первый опрос — до ответа: всё, что он видит, клиент получит снимком REST после `open`,
    # а изменения после него — событиями.
    opened = open_feed(connected_at)
    state = start_feed(opened, await read(opened))

    async def body() -> AsyncIterator[str]:
        yield _RETRY
        async for item in run_feed_events(state, read, sleep=sleep, session_alive=session_alive, clock=clock):
            yield format_feed_event(item)

    return _EventStream(body(), media_type="text/event-stream", headers=_HEADERS)
