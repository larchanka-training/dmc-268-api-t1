"""Чтение зарегистрированных репозиториев.

Транспорт: разбор запроса, вызов порта, формат ответа. Пагинация —
`limit`/`offset`, превышение потолка приводится к потолку, а не 400: см.
design.md change'а add-review-rest-api.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.dependencies import get_unit_of_work
from app.application.ports import UnitOfWork

router = APIRouter(prefix="/api/v1/repositories", tags=["repositories"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class RepositoryResponse(BaseModel):
    id: UUID
    provider: str
    provider_id: str
    full_name: str
    default_branch: str
    auto_review_enabled: bool
    created_at: datetime
    updated_at: datetime


class RepositoryListResponse(BaseModel):
    items: list[RepositoryResponse]


@router.get("", response_model=RepositoryListResponse)
def list_repositories(
    limit: int = Query(default=DEFAULT_LIMIT, ge=1),
    offset: int = Query(default=0, ge=0),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> RepositoryListResponse:
    repositories = uow.repositories.list_all(min(limit, MAX_LIMIT), offset)
    return RepositoryListResponse(
        items=[
            RepositoryResponse(
                id=r.id,
                provider=r.provider.value,
                provider_id=r.provider_id,
                full_name=r.full_name,
                default_branch=r.default_branch,
                auto_review_enabled=r.auto_review_enabled,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in repositories
        ]
    )
