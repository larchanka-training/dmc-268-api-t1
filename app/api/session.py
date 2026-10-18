"""Сессия в cookie: проверка на входе в ручку и ответы `401` и `403`.

Проверка — зависимости FastAPI, а не middleware: какие ручки их требуют,
видно в объявлении роутеров. `401` и `403` — подклассы `ApiError`: зависимость
не может вернуть ответ сама, а тело собирает общий обработчик. `401` ещё и
удаляет недействительную cookie — в своём `finish`.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import Depends, Request, Response, Security
from fastapi.security import APIKeyCookie

from app.api.dependencies import get_settings, get_unit_of_work_factory
from app.api.errors import ERRORS_401_SESSION, ApiError
from app.api.problems import ErrorCode
from app.application.ports import UnitOfWork
from app.application.use_cases.auth import authenticate
from app.config import Settings
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
        "PATCH, DELETE) дополнительно сверяют `Origin` с адресом сервиса, иначе `403 ORIGIN_FORBIDDEN`."
    ),
    auto_error=False,
)
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class Unauthenticated(ApiError):
    """Нет действительной сессии. `had_cookie` — cookie пришла, её надо удалить."""

    def __init__(self, had_cookie: bool) -> None:
        super().__init__(401, ErrorCode.UNAUTHENTICATED, "Нет действительной сессии.")
        self.had_cookie = had_cookie

    def finish(self, response: Response) -> None:
        if self.had_cookie:
            clear_session_cookie(response)


class OriginForbidden(ApiError):
    """Изменяющий запрос пришёл без `Origin` или с чужим."""

    def __init__(self) -> None:
        super().__init__(
            403,
            ErrorCode.ORIGIN_FORBIDDEN,
            "Изменяющий запрос пришёл без Origin или с Origin другого сайта.",
        )


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
    token: str | None = Security(_SESSION_SCHEME),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> User:
    """Пользователь сессии из cookie или `401`."""
    user = authenticate(uow_factory(), token, datetime.now(UTC))
    if user is None:
        raise Unauthenticated(had_cookie=token is not None)
    return user


# Порядок важен: чужой `Origin` отклоняется до похода в базу.
SESSION_REQUIRED = [Depends(require_origin), Depends(current_user)]
# Ответ `401` в схеме роутеров, которые требуют сессию.
SESSION_RESPONSES = ERRORS_401_SESSION

