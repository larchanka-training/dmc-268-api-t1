# Шаблон: эндпоинт

Роутер в `app/api/` отвечает за транспорт: разбор запроса, вызов сценария, формат ответа.
Бизнес-правил в нём нет.

```python
# app/api/<область>.py
from http import HTTPStatus
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import Field

from app.api.errors import ERRORS_403_ORIGIN, ERRORS_409_<КОД>, ERRORS_422, ApiError
from app.api.problems import ErrorCode
from app.api.schemas import CamelCaseRequest, CamelCaseResponse, UtcDatetime

router = APIRouter(prefix="/api/v1/<область>", tags=["<область>"])


class <Что>Request(CamelCaseRequest):
    """<что просит клиент>"""

    <поле>: str = Field(max_length=255, description="<смысл, если не очевиден из имени>")


class <Что>Response(CamelCaseResponse):
    """<что это за сущность — попадёт в description схемы>"""

    id: UUID
    status: <Enum домена> = Field(examples=["<значение>"])
    created_at: UtcDatetime  # время — только UtcDatetime: UTC с `Z`


@router.post(
    "",
    status_code=HTTPStatus.CREATED,  # статус — только HTTPStatus, не число
    response_model=<Что>Response,
    summary="<что делает ручка>",
    description="<что делает; сессия и Origin; особенности>",
    response_description="<что в успешном ответе>",
    # Все ошибки операции — моделями; `default` с каталогом подключён ко всему приложению.
    # `Origin` изменяющего запроса проверяет middleware, здесь — только описание его 403.
    responses={**ERRORS_403_ORIGIN, **ERRORS_409_<КОД>, **ERRORS_422},
)
def <действие>(body: <Что>Request, uow=Depends(get_uow)) -> <Что>Response:
    verdict = <сценарий>(body.<поле>, uow)
    if not verdict.ok:
        # Отказ — только ApiError: тело Problem Details (RFC 9457) собирает общий
        # обработчик. detail — по-русски и без значений из запроса.
        raise ApiError(HTTPStatus.CONFLICT, ErrorCode.<КОД>, "<что случилось в этом запросе>")
    # Ручка возвращает модель ответа — не словарь и не `JSONResponse` с данными. Поля
    # совпадают с сущностью — `model_validate(..., from_attributes=True)`.
    return <Что>Response.model_validate(verdict.unwrap(), from_attributes=True)
```

Роутер подключается в `create_app` (`app/api/factory.py`). После — экспорт контракта:
`uv run python scripts/export_openapi.py`, `docs/openapi/openapi.json` — в тот же коммит.

Ошибка — `raise ApiError(статус, ErrorCode.<КОД>, detail)`, а не `HTTPException` и не
свой `JSONResponse`: тело `application/problem+json` с `type`, `title`, `status`, `detail`,
`instance`, `code` и `trace_id` строит `problem_response` в `app/api/errors.py` через модель
кода. Новый код — в `app/api/problems.py` и `app/api/errors.py`:

```python
# app/api/problems.py
class ErrorCode(StrEnum):
    ...
    <КОД> = "<КОД>"


class <Что>Problem(Problem):
    """`409 <КОД>`: <когда>."""

    model_config = ConfigDict(
        json_schema_extra=_example(ErrorCode.<КОД>, "<Заголовок>", HTTPStatus.CONFLICT, "<detail>", "/api/v1/<область>")
    )

    type: Literal["urn:dmc-268:problem:<код-в-kebab-case>"] = "urn:dmc-268:problem:<код-в-kebab-case>"
    title: Literal["<Заголовок>"] = "<Заголовок>"
    status: Literal[HTTPStatus.CONFLICT] = HTTPStatus.CONFLICT
    code: Literal[ErrorCode.<КОД>] = ErrorCode.<КОД>

# ...и `<Что>Problem` — вариант объединения в `AnyProblem`.

# app/api/errors.py
ERRORS_409_<КОД> = _doc(HTTPStatus.CONFLICT, <Что>Problem, "<когда, для схемы>")
```

Расхождение кода, модели и схемы ловят `tests/test_docs.py` и `tests/test_problem_details.py`.

Запланированная ручка — тот же роут с полной схемой, но тело —
`raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "<что не реализовано>")`, в `responses` —
`ERRORS_501_NOT_IMPLEMENTED`, в `description` — «Не реализовано: отвечает 501 NOT_IMPLEMENTED».

Тест без базы:

```python
# tests/test_<область>.py
from fastapi.testclient import TestClient


def test_<действие>_returns_409_on_refusal(client: TestClient) -> None:
    response = client.post("/api/v1/<область>", json={"<поле>": "<значение>"})
    assert response.status_code == 409
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "<КОД>"
```

Проверка: `uv run lint-imports` — роутер не тянет SQLAlchemy напрямую, только через порт.
