# Шаблон: эндпоинт

Роутер в `app/api/` отвечает за транспорт: разбор запроса, вызов сценария, формат ответа.
Бизнес-правил в нём нет.

```python
# app/api/<область>.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/<область>", tags=["<область>"])


class <Что>Request(BaseModel):
    <поле>: str


class <Что>Response(BaseModel):
    id: str
    status: str


@router.post("/", response_model=<Что>Response)
def <действие>(body: <Что>Request, uow=Depends(get_uow)) -> <Что>Response:
    verdict = <сценарий>(body.<поле>, uow)
    if not verdict.ok:
        raise HTTPException(status_code=409, detail=verdict.error)
    return <Что>Response(id=str(verdict.unwrap().id), status=verdict.unwrap().status.value)
```

Роутер подключается в `create_app` (`app/api/factory.py`).

Тест без базы:

```python
# tests/test_<область>.py
from fastapi.testclient import TestClient


def test_<действие>_returns_409_on_refusal(client: TestClient) -> None:
    response = client.post("/<область>/", json={"<поле>": "<значение>"})
    assert response.status_code == 409
```

Проверка: `uv run lint-imports` — роутер не тянет SQLAlchemy напрямую, только через порт.
