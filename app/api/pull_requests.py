"""Чтение запросов на изменение репозитория.

Транспорт, как в `app/api/repositories.py`. 404, если репозитория нет —
отдельного эндпоинта на "список PR по всем репозиториям" нет: фронтенд
листает от репозитория, как `MergeRequestRepo.find_by_number` уже требует
`repository_id`.
"""

from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from app.api.dependencies import (
    get_installation_catalog,
    get_token_provider,
    get_unit_of_work_factory,
)
from app.api.errors import ERRORS_404_REPOSITORY, ERRORS_422, ERRORS_502_SCM_VISIBILITY
from app.api.pagination import DEFAULT_LIMIT, Limit, Offset, page_size
from app.api.repositories import visible_repository
from app.api.schemas import MergeRequestBase, UtcDatetime
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES, current_session
from app.application.ports import InstallationCatalog, OAuthProvider, UnitOfWork
from app.application.use_cases.auth import SignedIn

router = APIRouter(
    prefix="/api/v1/repositories",
    tags=["pull-requests"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


class MergeRequestResponse(MergeRequestBase):
    """Запрос на изменение (pull request GitHub) — каким его видел сервис при последнем вебхуке."""

    description: str = Field(description="Описание запроса на изменение; пустая строка, если его нет.")
    head_sha: str = Field(
        description="Последний коммит ветки с изменениями.", examples=["a1b2c3d4e5f6789012345678abcdef0123456789"]
    )
    created_at: UtcDatetime = Field(description="Когда сервис впервые записал запрос на изменение.")
    updated_at: UtcDatetime = Field(description="Когда запись последний раз менялась.")


class MergeRequestListResponse(BaseModel):
    """Страница списка запросов на изменение."""

    items: list[MergeRequestResponse] = Field(
        description="Запросы на изменение страницы; меньше limit — значит, страница последняя."
    )


@router.get(
    "/{repository_id}/pull-requests",
    response_model=MergeRequestListResponse,
    summary="Список запросов на изменение репозитория",
    response_description="Страница запросов на изменение.",
    description=(
        "Требует сессию. Постранично, по номеру запроса на изменение. Пагинация та же, что у "
        "списка репозиториев: limit выше 200 приводится к 200. Репозитория нет или пользователь не "
        "видит его на GitHub — один и тот же 404."
    ),
    responses={**ERRORS_404_REPOSITORY, **ERRORS_422, **ERRORS_502_SCM_VISIBILITY},
)
def list_pull_requests(
    repository_id: Annotated[UUID, Path(description="Идентификатор репозитория в сервисе.")],
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_installation_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> MergeRequestListResponse:
    visible_repository(repository_id, signed_in, catalog, oauth, uow_factory)
    with uow_factory() as uow:
        merge_requests = uow.merge_requests.list_for_repository(repository_id, page_size(limit), offset)
    return MergeRequestListResponse(
        items=[MergeRequestResponse.model_validate(mr, from_attributes=True) for mr in merge_requests]
    )
