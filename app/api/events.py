"""Поток событий кабинета по Server-Sent Events (пока заглушка).

Один поток на всё: прогоны сейчас, другие ресурсы потом. Имя события —
`<ресурс>.<действие>`, данные — модель своего типа (`x-sse-events`); у `run.*` —
`ReviewRunListItem` из `app/api/reviews.py`.
"""

from http import HTTPStatus
from typing import Any, Literal

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import RootModel

from app.api.errors import ERRORS_501_NOT_IMPLEMENTED, ApiError
from app.api.problems import ErrorCode
from app.api.reviews import VISIBILITY_RULE
from app.api.schemas import STREAM_HEADERS_DOC
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES

_STREAM_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #103). Требует сессию. Когда ручка заработает: один поток Server-Sent Events на весь "
    "кабинет, без параметров, сейчас для прогонов ревью, дальше и для других ресурсов. Заменит поток "
    "одного прогона GET /reviews/{review_run_id}/events. Имя события — `<ресурс>.<действие>` в поле "
    "`event:`, значения — StreamEventType; данные — JSON модели своего типа в `data:` одной строкой, "
    "какая модель у какого типа — в `x-sse-events` ответа. Сейчас типы такие: `run.created` — новый "
    "прогон; `run.status_changed` — прогон сменил статус, но не на итоговый; `run.finished` — прогон "
    "пришёл в итоговый статус (completed, failed или cancelled), при этом переходе приходит только "
    "`run.finished`. Данные `run.*` — ReviewRunListItem, полная строка списка GET /reviews. `ping` с "
    "`data: {}` — в тишине, не реже раза в 15 секунд. Клиент игнорирует незнакомые типы событий: новые "
    "типы добавляются без правки клиентов. Фильтров у потока нет: клиент получает все видимые ему "
    "события и отбирает нужные сам. Видимость задаёт правило типа события, пользователь получает "
    "только видимые ему события. Для `run.*`: " + VISIBILITY_RULE + " Набор видимых репозиториев "
    "считается при подключении токеном пользователя у провайдера; доступ, отобранный у провайдера, "
    "учитывается при переподключении. Клиент применяет любое событие прогона одинаково: upsert по id, "
    "из двух версий побеждает та, у которой updated_at больше, при равном — пришедшая позже. События "
    "уходят в момент коммита записи прогона (PostgreSQL NOTIFY из транзакции записи): откаченная "
    "запись события не даёт. Истории сервер не хранит: при подключении и после каждого "
    "переподключения клиент перезапрашивает снимок REST (GET /reviews/{review_run_id} или "
    "GET /reviews) после события `open` у EventSource; событие, пришедшее раньше снимка, не "
    "потеряется, а более старая версия строки проиграет по updated_at. Поток начинается со строки "
    "`retry: 3000`."
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

# Сессия — на весь роутер, как у остальных ручек `/api/v1`.
router = APIRouter(
    prefix="/api/v1/events",
    tags=["events"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


@router.get(
    "",
    response_class=StreamingResponse,
    summary="Поток событий кабинета (Server-Sent Events)",
    description=_STREAM_DESCRIPTION,
    responses={**_STREAM_DOC, **ERRORS_501_NOT_IMPLEMENTED},
)
def stream_events() -> StreamingResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Поток событий ещё не реализован.")
