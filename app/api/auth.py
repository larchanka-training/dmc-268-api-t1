"""Вход через OAuth, текущий пользователь, выход и возврат с установки GitHub App.

`login` и `callback` открыты: сессии ещё нет. Выход идемпотентен и сессии не
требует, только `Origin`: без сессии выходить уже не из чего, и это не ошибка. `state` и проверенный
`redirect` между ними живут в cookie `oauth_state` — 10 минут и только на
путях `/api/v1/auth`. `redirect` из неё проверяется ещё раз в use case:
cookie приходит от клиента. Любая ошибка callback — `302` на
`/login?error=<код>`. Возврат с установки (Setup URL) после `setup_action=install` подключает
выбранные репозитории установки и уводит на «Репозитории» с итогом; без сессии — на вход с возвратом туда же
(change `repositories-one-list`, design D7).
"""

import logging
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
    get_refreshing_catalog,
    get_token_provider,
    get_unit_of_work_factory,
)
from app.api.errors import (
    ERRORS_403_ORIGIN,
    ERRORS_404_LOGIN_PROVIDER,
    ERRORS_422,
    ApiError,
)
from app.api.problems import ErrorCode
from app.api.session import (
    SESSION_COOKIE,
    SESSION_RESPONSES,
    clear_session_cookie,
    current_user,
    set_session_cookie,
)
from app.application.ports import InstallationCatalog, OAuthProvider, UnitOfWork
from app.application.use_cases import connect_repository as connecting
from app.application.use_cases.auth import (
    LoginFailed,
    authenticate,
    complete_login,
    logout,
    start_login,
)
from app.application.use_cases.user_token import SessionClosed, revoke_quietly
from app.domain.entities import User
from app.domain.enums import Provider
from app.domain.ids import new_id
from app.domain.login import OAuthProviderUnavailable
from app.domain.session import setup_state_matches
from app.domain.vcs_errors import VcsError

_log = logging.getLogger(__name__)

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


# Setup URL GitHub App — задача #243 (бэкенд-часть dmc-268-ui-t1#95), design D7.
_SETUP_DESCRIPTION = (
    "Setup URL GitHub App: сюда GitHub возвращает браузер после установки приложения, её "
    "изменения или запроса владельцу. Требует сессию; без неё или с недействительной cookie "
    "session — на вход. `setup_action=request` — ничего не подключает. `install` требует `state` из "
    "`install_url` списка GET /repositories, выданный этой сессии: без него или с чужим — "
    "ничего не подключает (`?connected=0`), к GitHub не обращается; `update` `state` не "
    "проверяет: он ничего не подключает. `installation_id` не "
    "доверяется: ищется среди установок пользователя по его токену (`GET /user/installations`), "
    "свежий список видимых репозиториев пишется в кэш (сбой этого шага подключению не мешает). "
    "Подключает только `setup_action=install` при «Only select repositories»: репозитории "
    "установки, где у пользователя admin или maintain и которых в сервисе ещё нет; выключенные "
    "не включает. `setup_action=update` и «All repositories» ничего не подключают "
    "(`?connected=0`), кэш обновляется. Токен пользователя непригоден — сессия закрывается, "
    "редирект на вход."
)
# Куда вернуть после входа, если сессии нет: та же страница, куда ведёт итог.
_SETUP_LOGIN = "/login?redirect=/app/repositories"
# Итог — только на свою страницу: адрес собирается из констант и числа.
_SETUP_DONE = "/app/repositories"


@router.get(
    "/github/setup",
    status_code=HTTPStatus.FOUND,
    response_class=RedirectResponse,
    openapi_extra={"security": [{"session": []}]},
    summary="Возврат с установки GitHub App",
    description=_SETUP_DESCRIPTION,
    responses={
        HTTPStatus.FOUND: {
            "description": (
                "Редирект на /app/repositories с итогом: `?connected=<N>`, `?requested=true`, "
                "`?setup_error=installation_not_found` или `?setup_error=provider_unavailable`. Без "
                "сессии — на /login?redirect=/app/repositories, недействительная cookie session удаляется."
            ),
            "headers": _LOCATION,
        },
        **ERRORS_422,
    },
)
def finish_github_setup(
    request: Request,
    installation_id: int | None = Query(
        default=None,
        gt=0,
        description="Id установки от GitHub; не доверяется, сверяется с установками пользователя. Нет при `request`.",
    ),
    setup_action: Literal["install", "update", "request"] = Query(
        description="Что сделал пользователь на GitHub: установил, изменил установку или запросил её у владельца."
    ),
    state: str | None = Query(
        default=None,
        max_length=128,
        description="`state` из `install_url`, который GitHub возвращает на Setup URL; привязан к сессии.",
    ),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
    catalog: InstallationCatalog = Depends(get_refreshing_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    now = datetime.now(UTC)
    signed_in = authenticate(uow_factory(), token, now)
    if token is None or signed_in is None:
        return _setup_login(request)
    if setup_action == "request":
        return _setup_done("requested=true")
    if installation_id is None:
        return _setup_done("setup_error=installation_not_found")
    # Подделанный переход с чужого сайта `state` этой сессии не знает: ничего не подключаем.
    # Итог тот же, что «нечего подключать», а запись в логе показывает на стенде, вернул ли
    # GitHub `state` (design D7). Значение `state` в лог не пишется. `update` ничего не
    # подключает, только обновляет кэш, и `state` ему не нужен.
    if setup_action == "install" and (state is None or not setup_state_matches(token, state)):
        _log.warning("Setup URL: state не совпал (%s), ничего не подключено", "нет" if state is None else "чужой")
        return _setup_done("connected=0")
    user = connecting.UserContext(oauth=oauth, session_id=signed_in.session_id)
    try:
        connected = connecting.connect_installation(
            catalog, uow_factory(), user, installation_id, new_id, now, connect_selected=setup_action == "install"
        )
    except SessionClosed:
        return _setup_login(request)
    except (VcsError, OAuthProviderUnavailable) as error:
        # Текст ошибки несёт HTTP-статус GitHub: `403` (например, SSO организации не
        # авторизован для токена) так отличим от недоступного GitHub.
        _log.warning("Setup URL: GitHub отказал: %s", error)
        return _setup_done("setup_error=provider_unavailable")
    if connected is None:
        return _setup_done("setup_error=installation_not_found")
    return _setup_done(f"connected={connected}")


def _setup_login(request: Request) -> Response:
    """Браузер пришёл с GitHub, а не из кабинета: JSON `401` ему показать некому — на вход."""
    response = RedirectResponse(_SETUP_LOGIN, status_code=HTTPStatus.FOUND)
    if SESSION_COOKIE in request.cookies:
        clear_session_cookie(response)
    return response


def _setup_done(outcome: str) -> Response:
    return RedirectResponse(f"{_SETUP_DONE}?{outcome}", status_code=HTTPStatus.FOUND)


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
