"""Зарегистрированные репозитории: чтение и подключение.

Транспорт: разбор запроса, вызов порта или use case, формат ответа. Пагинация —
`app/api/pagination.py`. Правила подключения — `app/application/use_cases/connect_repository.py`.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies import (
    get_installation_catalog,
    get_unit_of_work,
    get_unit_of_work_factory,
)
from app.api.errors import (
    ERRORS_403_ORIGIN_OR_ADMIN,
    ERRORS_404_NOT_IN_INSTALLATION,
    ERRORS_409_ALREADY_CONNECTED,
    ERRORS_422,
    ERRORS_502_SCM_REPOSITORIES,
    ApiError,
)
from app.api.pagination import DEFAULT_LIMIT, Limit, Offset, page_size
from app.api.problems import ErrorCode
from app.api.schemas import RepositoryBase, StrictRequest, UtcDatetime
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES, current_user
from app.application.ports import InstallationCatalog, UnitOfWork

# Под своими именами: имена функций роутов — operationId в схеме, их держат типы фронтенда.
from app.application.use_cases import connect_repository as connecting
from app.domain.entities import User
from app.domain.enums import Provider
from app.domain.ids import new_id
from app.domain.vcs_errors import VcsError

# Форма — вариант A решения 3 в dmc-268-ui-t1#37; поведение — change `implement-repository-connect`.
_AVAILABLE_DESCRIPTION = (
    "Требует сессию. Репозитории всех установок GitHub App с отметкой `already_connected` — "
    "зарегистрирован ли репозиторий в сервисе. По правам вошедшего пользователя список не "
    "фильтруется: запрос прав на каждый репозиторий при каждом показе дорог, права проверяет "
    "подключение. Если GitHub не ответил или ответил ошибкой — `502 SCM_UNAVAILABLE`."
)
_CONNECT_DESCRIPTION = (
    "Требует сессию и `Origin` сервиса; `Origin` проверяется раньше сессии и разбора тела. "
    "Регистрирует репозиторий установки GitHub App с `auto_review_enabled = true`; `full_name` "
    "и `default_branch` — из данных установки. Проверки по порядку: провайдер не `github` или "
    "репозитория нет среди репозиториев установок — `404 REPOSITORY_NOT_IN_INSTALLATION`; "
    "репозиторий уже подключён — `409 REPOSITORY_ALREADY_CONNECTED`; у пользователя нет прав "
    "admin на репозиторий у GitHub — `403 REPOSITORY_ADMIN_REQUIRED`. Если GitHub не ответил "
    "или ответил ошибкой — `502 SCM_UNAVAILABLE`, репозиторий не записан."
)
_SCM_UNAVAILABLE_DETAIL = "GitHub не ответил или ответил ошибкой."
# Отказ use case — ответ ручки.
_REFUSALS: dict[connecting.ConnectRefusal, tuple[HTTPStatus, ErrorCode, str]] = {
    connecting.ConnectRefusal.NOT_IN_INSTALLATION: (
        HTTPStatus.NOT_FOUND,
        ErrorCode.REPOSITORY_NOT_IN_INSTALLATION,
        "Репозитория нет среди репозиториев установок GitHub App.",
    ),
    connecting.ConnectRefusal.ALREADY_CONNECTED: (
        HTTPStatus.CONFLICT,
        ErrorCode.REPOSITORY_ALREADY_CONNECTED,
        "Репозиторий уже подключён.",
    ),
    connecting.ConnectRefusal.ADMIN_REQUIRED: (
        HTTPStatus.FORBIDDEN,
        ErrorCode.REPOSITORY_ADMIN_REQUIRED,
        "Подключать репозиторий может только пользователь с правами admin на него.",
    ),
}

# Сессия — на весь роутер: исключения видны здесь, а не в middleware.
router = APIRouter(
    prefix="/api/v1/repositories",
    tags=["repositories"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


class RepositoryResponse(RepositoryBase):
    """Репозиторий, зарегистрированный в сервисе: его запросы на изменение ревьюятся."""

    provider_id: str = Field(description="Идентификатор репозитория у провайдера.", examples=["123456789"])
    default_branch: str = Field(description="Ветка по умолчанию у провайдера.", examples=["main"])
    auto_review_enabled: bool = Field(description="Ревью запускается автоматически по вебхуку.")
    created_at: UtcDatetime = Field(description="Когда репозиторий зарегистрирован.")
    updated_at: UtcDatetime = Field(description="Когда запись последний раз менялась.")


class RepositoryListResponse(BaseModel):
    """Страница списка репозиториев."""

    items: list[RepositoryResponse] = Field(
        description="Репозитории страницы; меньше limit — значит, страница последняя."
    )


class AvailableRepositoryResponse(BaseModel):
    """Репозиторий у провайдера, который вошедший пользователь может подключить к сервису."""

    provider: Provider = Field(description="Хостинг репозитория.", examples=["github"])
    provider_id: str = Field(description="Идентификатор репозитория у провайдера.", examples=["123456789"])
    full_name: str = Field(description="Полное имя у провайдера: владелец/репозиторий.", examples=["octo-org/api"])
    default_branch: str = Field(description="Ветка по умолчанию у провайдера.", examples=["main"])
    already_connected: bool = Field(description="Репозиторий уже зарегистрирован в сервисе.")


class AvailableRepositoryListResponse(BaseModel):
    """Репозитории, доступные для подключения."""

    items: list[AvailableRepositoryResponse] = Field(description="Репозитории установки GitHub App.")


class ConnectRepositoryRequest(StrictRequest):
    """Запрос на подключение репозитория к сервису."""

    # strict у модели не пропустил бы enum строкой; для enum-поля — точечно, как в CreateReviewRequest.
    provider: Provider = Field(strict=False, description="Хостинг репозитория.", examples=["github"])
    provider_id: str = Field(
        min_length=1,
        max_length=255,
        description="Идентификатор репозитория у провайдера (из списка доступных).",
        examples=["123456789"],
    )


@router.get(
    "/available",
    response_model=AvailableRepositoryListResponse,
    summary="Репозитории, доступные для подключения",
    response_description="Репозитории установок GitHub App.",
    description=_AVAILABLE_DESCRIPTION,
    responses=ERRORS_502_SCM_REPOSITORIES,
)
def list_available_repositories(
    catalog: InstallationCatalog = Depends(get_installation_catalog),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> AvailableRepositoryListResponse:
    try:
        available = connecting.list_available_repositories(catalog, uow_factory())
    except VcsError as error:
        raise ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.SCM_UNAVAILABLE, _SCM_UNAVAILABLE_DETAIL) from error
    return AvailableRepositoryListResponse(
        items=[
            AvailableRepositoryResponse(
                provider=Provider.GITHUB,
                provider_id=item.repository.provider_id,
                full_name=item.repository.full_name,
                default_branch=item.repository.default_branch,
                already_connected=item.already_connected,
            )
            for item in available
        ]
    )


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    response_model=RepositoryResponse,
    summary="Подключить репозиторий",
    response_description="Репозиторий зарегистрирован.",
    description=_CONNECT_DESCRIPTION,
    responses={
        **ERRORS_403_ORIGIN_OR_ADMIN,
        **ERRORS_404_NOT_IN_INSTALLATION,
        **ERRORS_409_ALREADY_CONNECTED,
        **ERRORS_422,
        **ERRORS_502_SCM_REPOSITORIES,
    },
)
def connect_repository(
    body: ConnectRepositoryRequest,
    user: User = Depends(current_user),
    catalog: InstallationCatalog = Depends(get_installation_catalog),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> RepositoryResponse:
    try:
        outcome = connecting.connect_repository(
            catalog, uow_factory(), body.provider, body.provider_id, user.login, new_id(), datetime.now(UTC)
        )
    except VcsError as error:
        raise ApiError(HTTPStatus.BAD_GATEWAY, ErrorCode.SCM_UNAVAILABLE, _SCM_UNAVAILABLE_DETAIL) from error
    if isinstance(outcome, connecting.ConnectRefusal):
        raise ApiError(*_REFUSALS[outcome])
    return RepositoryResponse.model_validate(outcome, from_attributes=True)


@router.get(
    "",
    response_model=RepositoryListResponse,
    summary="Список зарегистрированных репозиториев",
    response_description="Страница репозиториев.",
    description=(
        "Требует сессию. Постранично, по времени регистрации, затем по id. limit выше 200 не "
        "отклоняется, а приводится к 200. Общего числа записей в ответе нет: конец списка — "
        "страница короче limit."
    ),
    responses=ERRORS_422,
)
def list_repositories(
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> RepositoryListResponse:
    repositories = uow.repositories.list_all(page_size(limit), offset)
    return RepositoryListResponse(
        items=[RepositoryResponse.model_validate(r, from_attributes=True) for r in repositories]
    )
