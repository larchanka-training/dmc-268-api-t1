"""Вход через OAuth, текущий пользователь и выход.

`login` и `callback` открыты: сессии ещё нет. Выход идемпотентен и сессии не
требует, только `Origin`: без сессии выходить уже не из чего, и это не ошибка. `state` и проверенный
`redirect` между ними живут в cookie `oauth_state` — 10 минут и только на
путях `/api/v1/auth`. `redirect` из неё проверяется ещё раз в use case:
cookie приходит от клиента. Любая ошибка callback — `302` на
`/login?error=<код>`.
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, unquote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.api.dependencies import get_oauth_provider, get_unit_of_work_factory
from app.api.errors import (
    ERRORS_403_ORIGIN,
    ERRORS_404_LOGIN_PROVIDER,
    ApiError,
)
from app.api.problems import ErrorCode
from app.api.session import (
    SESSION_COOKIE,
    SESSION_RESPONSES,
    clear_session_cookie,
    current_user,
    require_origin,
    set_session_cookie,
)
from app.application.ports import OAuthProvider, UnitOfWork
from app.application.use_cases.auth import (
    LoginFailed,
    complete_login,
    logout,
    start_login,
)
from app.domain.entities import User
from app.domain.enums import Provider
from app.domain.ids import new_id

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

STATE_COOKIE = "oauth_state"
_STATE_PATH = "/api/v1/auth"
_STATE_MAX_AGE = 600
# `state` — token_urlsafe, в нём `|` не встречается; `redirect` экранирован.
_SEPARATOR = "|"


# Ручка открыта без сессии — в схеме явно, а не пропуском.
_PUBLIC: dict[str, Any] = {"security": []}
_LOCATION = {"Location": {"description": "Куда перенаправлен браузер.", "schema": {"type": "string"}}}


class UserResponse(BaseModel):
    provider: Provider
    login: str
    name: str | None
    avatar_url: str | None


def _known(provider: OAuthProvider | None) -> OAuthProvider:
    if provider is None:
        raise ApiError(
            404, ErrorCode.PROVIDER_NOT_SUPPORTED, "Вход через этого провайдера не поддержан."
        )
    return provider


def _clear_state_cookie(response: Response) -> None:
    response.delete_cookie(
        STATE_COOKIE, path=_STATE_PATH, secure=True, httponly=True, samesite="lax"
    )


def _read_state_cookie(value: str | None) -> tuple[str | None, str | None]:
    if not value or _SEPARATOR not in value:
        return None, None
    state, redirect = value.split(_SEPARATOR, 1)
    return state, unquote(redirect)


@router.get(
    "/{provider}/login",
    status_code=302,
    response_class=RedirectResponse,
    openapi_extra=_PUBLIC,
    summary="Начать вход через провайдера",
    description=(
        "Открыт без сессии. Ставит cookie oauth_state со state и проверенным redirect и "
        "перенаправляет на страницу авторизации провайдера. redirect принимается, только если "
        "это путь под /app/ не длиннее 1000 байт в UTF-8; иначе после входа — /app/."
    ),
    responses={
        302: {
            "description": (
                "Редирект на страницу авторизации провайдера с параметром state. Если вход у "
                "провайдера не настроен — редирект на /login?error=provider_unavailable без cookie."
            ),
            "headers": _LOCATION,
        },
        **ERRORS_404_LOGIN_PROVIDER,
    },
)
def begin_login(
    redirect: str | None = Query(default=None),
    provider: OAuthProvider | None = Depends(get_oauth_provider),
) -> Response:
    start = start_login(_known(provider), redirect, secrets.token_urlsafe(32))
    if isinstance(start, LoginFailed):
        return RedirectResponse(f"/login?error={start.error.value}", status_code=302)
    response = RedirectResponse(start.authorize_url, status_code=302)
    response.set_cookie(
        STATE_COOKIE,
        f"{start.state}{_SEPARATOR}{quote(start.redirect, safe='')}",
        max_age=_STATE_MAX_AGE,
        path=_STATE_PATH,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get(
    "/{provider}/callback",
    status_code=302,
    response_class=RedirectResponse,
    openapi_extra=_PUBLIC,
    summary="Завершить вход",
    description=(
        "Сюда провайдер возвращает браузер. Открыт без сессии. state сверяется с cookie "
        "oauth_state, code обменивается на профиль, пользователь находится или создаётся, "
        "создаётся сессия. Любая ошибка — редирект на /login?error=<код>: state_mismatch, "
        "denied, provider_unavailable."
    ),
    responses={
        302: {
            "description": (
                "Успех — редирект на redirect из oauth_state или на /app/ и cookie session. "
                "Ошибка — редирект на /login?error=<код>, сессии нет. oauth_state удаляется в обоих случаях."
            ),
            "headers": _LOCATION,
        },
        **ERRORS_404_LOGIN_PROVIDER,
    },
)
def finish_login(
    request: Request,
    code: str | None = Query(default=None),
    state: str | None = Query(default=None),
    error: str | None = Query(default=None),
    provider: OAuthProvider | None = Depends(get_oauth_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> Response:
    known = _known(provider)
    expected_state, redirect = _read_state_cookie(request.cookies.get(STATE_COOKIE))
    outcome = complete_login(
        uow_factory(),
        known,
        state=state,
        expected_state=expected_state,
        code=code,
        error=error,
        redirect=redirect,
        token=secrets.token_urlsafe(32),
        now=datetime.now(UTC),
        new_id=new_id,
        previous_token=request.cookies.get(SESSION_COOKIE),
    )
    if isinstance(outcome, LoginFailed):
        response = RedirectResponse(f"/login?error={outcome.error.value}", status_code=302)
    else:
        response = RedirectResponse(outcome.redirect, status_code=302)
        set_session_cookie(response, outcome.token)
    _clear_state_cookie(response)
    return response


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Пользователь текущей сессии",
    response_description="Профиль пользователя.",
    responses=SESSION_RESPONSES,
)
def me(user: User = Depends(current_user)) -> UserResponse:
    return UserResponse(
        provider=user.provider,
        login=user.login,
        name=user.name,
        avatar_url=user.avatar_url,
    )


@router.post(
    "/logout",
    status_code=204,
    dependencies=[Depends(require_origin)],
    openapi_extra=_PUBLIC,
    summary="Выйти",
    description=(
        "Удаляет сессию текущей cookie: следующий запрос с ней из любой вкладки получает 401. "
        "Идемпотентен: без cookie и с cookie неизвестной сессии тоже 204, cookie session "
        "удаляется. Сессия не нужна, проверяется только Origin."
    ),
    responses={204: {"description": "Сессия удалена, cookie session удалена."}, **ERRORS_403_ORIGIN},
)
def end_session(
    request: Request,
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> Response:
    logout(uow_factory(), request.cookies.get(SESSION_COOKIE))
    response = Response(status_code=204)
    clear_session_cookie(response)
    return response
