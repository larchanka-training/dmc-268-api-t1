# Шаблон: эндпоинт

Роутер в `app/api/` отвечает за транспорт: разбор запроса, вызов сценария, формат ответа.
Бизнес-правил в нём нет.

```python
# app/api/<область>.py
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.errors import ERRORS_409_<КОД>, ERRORS_422, ApiError
from app.api.problems import ErrorCode

router = APIRouter(prefix="/api/v1/<область>", tags=["<область>"])


class <Что>Request(BaseModel):
    <поле>: str


class <Что>Response(BaseModel):
    id: str
    status: str


@router.post(
    "",
    response_model=<Что>Response,
    summary="<что делает ручка>",
    response_description="<что в успешном ответе>",
    # Все ошибки операции — моделями; `default` с каталогом подключён ко всему приложению.
    responses={**ERRORS_409_<КОД>, **ERRORS_422},
)
def <действие>(body: <Что>Request, uow=Depends(get_uow)) -> <Что>Response:
    verdict = <сценарий>(body.<поле>, uow)
    if not verdict.ok:
        # Отказ — только ApiError: тело Problem Details (RFC 9457) собирает общий
        # обработчик. detail — по-русски и без значений из запроса.
        raise ApiError(409, ErrorCode.<КОД>, "<что случилось в этом запросе>")
    return <Что>Response(id=str(verdict.unwrap().id), status=verdict.unwrap().status.value)
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
        json_schema_extra=_example(ErrorCode.<КОД>, "<Заголовок>", 409, "<detail>", "/api/v1/<область>")
    )

    type: Literal["urn:dmc-268:problem:<код-в-kebab-case>"] = "urn:dmc-268:problem:<код-в-kebab-case>"
    title: Literal["<Заголовок>"] = "<Заголовок>"
    status: Literal[409] = 409
    code: Literal[ErrorCode.<КОД>] = ErrorCode.<КОД>

# ...и `<Что>Problem` — вариант объединения в `AnyProblem`.

# app/api/errors.py
ERRORS_409_<КОД> = _doc(409, <Что>Problem, "<когда, для схемы>")
```

Расхождение кода, модели и схемы ловят `tests/test_docs.py` и `tests/test_problem_details.py`.

Запланированная ручка — тот же роут с полной схемой, но тело —
`raise ApiError(501, ErrorCode.NOT_IMPLEMENTED, "<что не реализовано>")`, в `responses` —
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
