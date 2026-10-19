"""Чтение зарегистрированных репозиториев.

Транспорт: разбор запроса, вызов порта, формат ответа. Пагинация — `app/api/pagination.py`.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies import get_unit_of_work
from app.api.errors import ERRORS_422
from app.api.pagination import DEFAULT_LIMIT, Limit, Offset, page_size
from app.api.schemas import RepositoryBase, UtcDatetime
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES
from app.application.ports import UnitOfWork

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
