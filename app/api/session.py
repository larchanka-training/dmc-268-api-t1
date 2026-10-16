"""Сессия в cookie: проверка на входе в ручку и ответы `401` и `403`.

Проверка — зависимости FastAPI, а не middleware: какие ручки их требуют,
видно в объявлении роутеров. `401` и `403` — исключения с обработчиками в
`create_app`, потому что зависимость не может вернуть ответ сама, а `401`
должен ещё и удалить недействительную cookie.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse

from app.api.dependencies import get_settings, get_unit_of_work_factory
from app.application.ports import UnitOfWork
from app.application.use_cases.auth import authenticate
from app.config import Settings
from app.domain.entities import User
from app.domain.session import SESSION_LIFETIME

SESSION_COOKIE = "session"
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class Unauthenticated(Exception):
    """Нет действительной сессии. `had_cookie` — cookie пришла, её надо удалить."""

    def __init__(self, had_cookie: bool) -> None:
        super().__init__("нет действительной сессии")
        self.had_cookie = had_cookie


class OriginForbidden(Exception):
    """Изменяющий запрос пришёл без `Origin` или с чужим."""


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


def clear_session_cookie(response: Response) -> None:
    # Атрибуты те же, что при установке: иначе браузер считает это другой cookie.
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


def require_origin(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """`Origin` изменяющего запроса совпадает с публичным адресом сервиса."""
    if request.method not in _UNSAFE_METHODS:
        return
    origin = request.headers.get("Origin")
    if not settings.public_origin or origin != settings.public_origin:
        raise OriginForbidden()


def current_user(
    request: Request,
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> User:
    """Пользователь сессии из cookie или `401`."""
    token = request.cookies.get(SESSION_COOKIE)
    user = authenticate(uow_factory(), token, datetime.now(UTC))
    if user is None:
        raise Unauthenticated(had_cookie=token is not None)
    return user


# Порядок важен: чужой `Origin` отклоняется до похода в базу.
SESSION_REQUIRED = [Depends(require_origin), Depends(current_user)]


def unauthenticated_response(request: Request, error: Exception) -> Response:
    response = JSONResponse(
        status_code=401,
        content={"code": "UNAUTHENTICATED", "message": "требуется вход"},
    )
    if isinstance(error, Unauthenticated) and error.had_cookie:
        clear_session_cookie(response)
    return response


def origin_forbidden_response(request: Request, error: Exception) -> Response:
    return JSONResponse(
        status_code=403,
        content={"code": "ORIGIN_FORBIDDEN", "message": "запрос с чужого origin"},
    )
