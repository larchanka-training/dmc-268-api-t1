"""Вход через OAuth, текущий пользователь, выход и возврат с установки GitHub App.

`login` и `callback` открыты: сессии ещё нет. Выход идемпотентен и сессии не
требует, только `Origin`: без сессии выходить уже не из чего, и это не ошибка. `state` и проверенный
`redirect` между ними живут в cookie `oauth_state` — 10 минут и только на
путях `/api/v1/auth`. `redirect` из неё проверяется ещё раз в use case:
cookie приходит от клиента. Любая ошибка callback — `302` на
`/login?error=<код>`. Возврат с установки (Setup URL) требует сессию — пока
заглушка `501` (change `repositories-one-list`).
"""

import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Any, Literal
from urllib.parse import quote, unquote

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.api.dependencies import (
    get_oauth_provider,
    get_token_provider,
    get_unit_of_work_factory,
)
from app.api.errors import (
    ERRORS_403_ORIGIN,
    ERRORS_404_LOGIN_PROVIDER,
    ERRORS_422,
    ERRORS_501_NOT_IMPLEMENTED,
    ApiError,
)
from app.api.problems import ErrorCode
from app.api.session import (
    SESSION_COOKIE,
    SESSION_REQUIRED,
    SESSION_RESPONSES,
    clear_session_cookie,
    current_user,
    set_session_cookie,
)
from app.application.ports import OAuthProvider, UnitOfWork
from app.application.use_cases.auth import (
    LoginFailed,
    complete_login,
    logout,
    start_login,
)
from app.application.use_cases.user_token import revoke_quietly
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
    """Пользователь кабинета — учётная запись у провайдера входа."""

    provider: Provider = Field(description="Провайдер, через которого выполнен вход.", examples=["github"])
    login: str = Field(description="Логин у провайдера.", examples=["octocat"])
    name: str | None = Field(description="Отображаемое имя у провайдера; null, если не задано.")
    avatar_url: str | None = Field(
        description="Адрес аватара у провайдера; null, если его нет.",
        json_schema_extra={"format": "uri"},
        examples=["https://avatars.githubusercontent.com/u/1"],
    )


def _known(provider: OAuthProvider | None) -> OAuthProvider:
    if provider is None:
        raise ApiError(
            HTTPStatus.NOT_FOUND, ErrorCode.PROVIDER_NOT_SUPPORTED, "Вход через этого провайдера не поддержан."
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
    status_code=HTTPStatus.FOUND,
    response_class=RedirectResponse,
    openapi_extra=_PUBLIC,
    summary="Начать вход через провайдера",
    description=(
        "Открыт без сессии. Ставит cookie oauth_state со state и проверенным redirect и "
        "перенаправляет на страницу авторизации провайдера. redirect принимается, только если "
        "это путь под /app/ не длиннее 1000 байт в UTF-8; иначе после входа — /app/."
    ),
    responses={
        HTTPStatus.FOUND: {
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
    redirect: str | None = Query(default=None, description="Куда вернуть после входа: путь под /app/."),
    provider: OAuthProvider | None = Depends(get_oauth_provider),
) -> Response:
    start = start_login(_known(provider), redirect, secrets.token_urlsafe(32))
    if isinstance(start, LoginFailed):
        return RedirectResponse(f"/login?error={start.error.value}", status_code=HTTPStatus.FOUND)
    response = RedirectResponse(start.authorize_url, status_code=HTTPStatus.FOUND)
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
    status_code=HTTPStatus.FOUND,
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
        HTTPStatus.FOUND: {
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
    code: str | None = Query(default=None, description="Код авторизации от провайдера."),
    state: str | None = Query(default=None, description="state, выданный при начале входа."),
    error: str | None = Query(default=None, description="Ошибка от провайдера, например отказ пользователя."),
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
        response = RedirectResponse(f"/login?error={outcome.error.value}", status_code=HTTPStatus.FOUND)
    else:
        response = RedirectResponse(outcome.redirect, status_code=HTTPStatus.FOUND)
        set_session_cookie(response, outcome.token)
    _clear_state_cookie(response)
    return response


# Setup URL GitHub App — задача #243 (бэкенд-часть dmc-268-ui-t1#95): контракт первым.
_SETUP_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал "
    "типы (задача #243). Setup URL GitHub App: сюда GitHub возвращает браузер после установки "
    "приложения, её изменения (Redirect on update) или запроса установки владельцу организации. "
    "Требует сессию; параметры проверяются до ответа 501. Когда ручка заработает: "
    "`installation_id` из адреса не доверяется — сверяется с установками пользователя по его "
    "токену (`GET /user/installations`), а кэш видимых репозиториев заменяется свежим ответом "
    "GitHub. Установка с выбором «Only select repositories» — подключаются её репозитории, на "
    "которых у пользователя роль admin или maintain и которых ещё нет в сервисе; выключенные "
    "`DELETE` не включаются. С «All repositories» не подключается ничего: нужные включают "
    "переключателем. Итог — редирект на страницу «Репозитории»."
)


@router.get(
    "/github/setup",
    status_code=HTTPStatus.FOUND,
    response_class=RedirectResponse,
    dependencies=SESSION_REQUIRED,
    summary="Возврат с установки GitHub App",
    description=_SETUP_DESCRIPTION,
    responses={
        HTTPStatus.FOUND: {
            "description": (
                "Редирект на /app/repositories с итогом: `?connected=<N>` — сколько репозиториев "
                "подключено (при «All repositories» — 0); `?requested=true` — при "
                "`setup_action=request`, запрос отправлен владельцу, ничего не подключено; "
                "`?setup_error=installation_not_found` — установки нет среди установок "
                "пользователя или нет `installation_id`; `?setup_error=provider_unavailable` — "
                "GitHub не ответил. Токен пользователя непригоден — `401`, сессия закрыта."
            ),
            "headers": _LOCATION,
        },
        **SESSION_RESPONSES,
        **ERRORS_422,
        **ERRORS_501_NOT_IMPLEMENTED,
    },
)
def finish_github_setup(
    installation_id: int | None = Query(
        default=None,
        gt=0,
        description="Id установки от GitHub; не доверяется, сверяется с установками пользователя. Нет при `request`.",
    ),
    setup_action: Literal["install", "update", "request"] = Query(
        description="Что сделал пользователь на GitHub: установил, изменил установку или запросил её у владельца."
    ),
) -> Response:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Возврат с установки ещё не реализован.")


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Пользователь текущей сессии",
    description="Требует сессию. По ответу кабинет решает, показывать ли страницу входа.",
    response_description="Профиль пользователя.",
    responses=SESSION_RESPONSES,
)
def me(user: User = Depends(current_user)) -> UserResponse:
    return UserResponse.model_validate(user, from_attributes=True)


@router.post(
    "/logout",
    status_code=HTTPStatus.NO_CONTENT,
    openapi_extra=_PUBLIC,
    summary="Выйти",
    description=(
        "Удаляет сессию текущей cookie вместе с сохранённым токеном GitHub: следующий запрос с "
        "ней из любой вкладки получает 401. Токен после ответа отзывается у GitHub (best-effort: "
        "сбой отзыва выходу не мешает). "
        "Идемпотентен: без cookie и с cookie неизвестной сессии тоже 204, cookie session "
        "удаляется. Сессия не нужна, проверяется только Origin."
    ),
    responses={HTTPStatus.NO_CONTENT: {"description": "Сессия удалена, cookie session удалена."}, **ERRORS_403_ORIGIN},
)
def end_session(
    request: Request,
    background: BackgroundTasks,
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
    token_provider: OAuthProvider = Depends(get_token_provider),
) -> Response:
    revocable = logout(uow_factory(), request.cookies.get(SESSION_COOKIE))
    if revocable is not None:
        # После ответа: выход не ждёт GitHub, и его сбой выходу не мешает.
        background.add_task(revoke_quietly, token_provider, revocable)
    response = Response(status_code=HTTPStatus.NO_CONTENT)
    clear_session_cookie(response)
    return response
