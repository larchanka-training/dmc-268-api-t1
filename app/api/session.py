"""Сессия в cookie: проверка на входе в ручку и ответ `401`.

Проверка — зависимость FastAPI, а не middleware: какие ручки её требуют,
видно в объявлении роутеров. `401` — подкласс `ApiError`: зависимость
не может вернуть ответ сама, а тело собирает общий обработчик. Он ещё и
удаляет недействительную cookie — в своём `finish`. `Origin` изменяющих
запросов проверяет `OriginMiddleware` (`app/api/origin.py`) — раньше сессии.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus

from fastapi import Depends, Response, Security
from fastapi.security import APIKeyCookie

from app.api.dependencies import get_unit_of_work_factory
from app.api.errors import ERRORS_401_SESSION, ApiError
from app.api.problems import ErrorCode
from app.application.ports import UnitOfWork
from app.application.use_cases.auth import authenticate
from app.domain.entities import User
from app.domain.session import SESSION_LIFETIME

SESSION_COOKIE = "session"
# Только для схемы: cookie описана как схема безопасности `session`. Сама проверка — в
# `current_user`, отказ — `401 UNAUTHENTICATED`, а не ответ схемы безопасности FastAPI.
_SESSION_SCHEME = APIKeyCookie(
    name=SESSION_COOKIE,
    scheme_name="session",
    description=(
        "Сессия в httpOnly cookie, ставится callback'ом входа. Изменяющие запросы (POST, PUT, "
        "PATCH, DELETE) под /api/v1, кроме вебхуков, дополнительно сверяют `Origin` с адресом "
        "сервиса — раньше сессии и разбора тела, иначе `403 ORIGIN_FORBIDDEN`."
    ),
    auto_error=False,
)


class Unauthenticated(ApiError):
    """Нет действительной сессии. `had_cookie` — cookie пришла, её надо удалить."""

    def __init__(self, had_cookie: bool) -> None:
        super().__init__(HTTPStatus.UNAUTHORIZED, ErrorCode.UNAUTHENTICATED, "Нет действительной сессии.")
        self.had_cookie = had_cookie

    def finish(self, response: Response) -> None:
        if self.had_cookie:
            clear_session_cookie(response)


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


def current_user(
    token: str | None = Security(_SESSION_SCHEME),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> User:
    """Пользователь сессии из cookie или `401`."""
    user = authenticate(uow_factory(), token, datetime.now(UTC))
    if user is None:
        raise Unauthenticated(had_cookie=token is not None)
    return user


# `Origin` к этому моменту уже проверен middleware.
SESSION_REQUIRED = [Depends(current_user)]
# Ответ `401` в схеме роутеров, которые требуют сессию.
SESSION_RESPONSES = ERRORS_401_SESSION

