"""Чтение зарегистрированных репозиториев.

Транспорт: разбор запроса, вызов порта, формат ответа. Пагинация — `app/api/pagination.py`.
"""

from http import HTTPStatus

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies import get_unit_of_work
from app.api.errors import (
    ERRORS_403_ORIGIN,
    ERRORS_422,
    ERRORS_501_NOT_IMPLEMENTED,
    ApiError,
)
from app.api.pagination import DEFAULT_LIMIT, Limit, Offset, page_size
from app.api.problems import ErrorCode
from app.api.schemas import CamelCaseRequest, RepositoryBase, UtcDatetime
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES
from app.application.ports import UnitOfWork
from app.domain.enums import Provider

# Реализация — задача #41; форма — вариант A решения 3 в dmc-268-ui-t1#37.
_AVAILABLE_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #41). Когда ручка заработает: репозитории установки GitHub App, доступные вошедшему "
    "пользователю, с отметкой, подключён ли репозиторий к сервису. Требует сессию."
)
_CONNECT_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #41). Когда ручка заработает: 201 с зарегистрированным репозиторием; подключать может "
    "только пользователь с правами admin на репозиторий у провайдера. Требует сессию и Origin "
    "сервиса; Origin проверяется раньше сессии и разбора тела."
)

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


class ConnectRepositoryRequest(CamelCaseRequest):
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
    response_description="Репозитории установки GitHub App.",
    description=_AVAILABLE_DESCRIPTION,
    responses=ERRORS_501_NOT_IMPLEMENTED,
)
def list_available_repositories() -> AvailableRepositoryListResponse:
    raise ApiError(
        HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Список доступных репозиториев ещё не реализован."
    )


@router.post(
    "",
    status_code=HTTPStatus.CREATED,
    response_model=RepositoryResponse,
    summary="Подключить репозиторий",
    response_description="Репозиторий зарегистрирован.",
    description=_CONNECT_DESCRIPTION,
    responses={**ERRORS_403_ORIGIN, **ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def connect_repository(body: ConnectRepositoryRequest) -> RepositoryResponse:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Подключение репозитория ещё не реализовано.")


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
