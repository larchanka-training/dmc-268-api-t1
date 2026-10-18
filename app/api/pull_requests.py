"""Чтение запросов на изменение репозитория.

Транспорт, как в `app/api/repositories.py`. 404, если репозитория нет —
отдельного эндпоинта на "список PR по всем репозиториям" нет: фронтенд
листает от репозитория, как `MergeRequestRepo.find_by_number` уже требует
`repository_id`.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.dependencies import get_unit_of_work
from app.api.errors import ERRORS_404_REPOSITORY, ERRORS_422, ApiError
from app.api.problems import ErrorCode
from app.api.repositories import DEFAULT_LIMIT, MAX_LIMIT
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES
from app.application.ports import UnitOfWork
from app.domain.enums import MergeRequestState

router = APIRouter(
    prefix="/api/v1/repositories",
    tags=["pull-requests"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


class MergeRequestResponse(BaseModel):
    id: UUID
    number: int
    title: str
    description: str
    author: str
    source_branch: str
    target_branch: str
    head_sha: str
    state: MergeRequestState
    created_at: datetime
    updated_at: datetime


class MergeRequestListResponse(BaseModel):
    items: list[MergeRequestResponse]


@router.get(
    "/{repository_id}/pull-requests",
    response_model=MergeRequestListResponse,
    summary="Список запросов на изменение репозитория",
    response_description="Страница запросов на изменение.",
    description="Постранично, по номеру запроса на изменение. Пагинация та же, что у списка репозиториев.",
    responses={**ERRORS_404_REPOSITORY, **ERRORS_422},
)
def list_pull_requests(
    repository_id: UUID,
    limit: int = Query(default=DEFAULT_LIMIT, ge=1, description="Размер страницы; больше 200 приводится к 200."),
    offset: int = Query(default=0, ge=0, description="Сколько записей пропустить."),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> MergeRequestListResponse:
    if uow.repositories.get(repository_id) is None:
        raise ApiError(404, ErrorCode.REPOSITORY_NOT_FOUND, "Репозитория с таким id нет.")

    merge_requests = uow.merge_requests.list_for_repository(
        repository_id, min(limit, MAX_LIMIT), offset
    )
    return MergeRequestListResponse(
        items=[
            MergeRequestResponse(
                id=mr.id,
                number=mr.number,
                title=mr.title,
                description=mr.description,
                author=mr.author,
                source_branch=mr.source_branch,
                target_branch=mr.target_branch,
                head_sha=mr.head_sha,
                state=mr.state,
                created_at=mr.created_at,
                updated_at=mr.updated_at,
            )
            for mr in merge_requests
        ]
    )
