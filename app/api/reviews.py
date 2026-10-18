"""Прогон ревью: чтение вместе с находками и ручной запуск (пока заглушка).

Один связный ответ, не два эндпоинта: прогон без находок бессмысленно
смотреть отдельно от них (design.md change'а add-review-rest-api).

`ReviewJob` — общая модель ответа ручного запуска и приёма вебхука, лежит в
`app/api/schemas.py`.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_unit_of_work
from app.api.errors import (
    ERRORS_403_ORIGIN,
    ERRORS_404_REVIEW,
    ERRORS_409_REVIEW_ACTIVE,
    ERRORS_422,
    ERRORS_501_NOT_IMPLEMENTED,
    ApiError,
)
from app.api.problems import ErrorCode
from app.api.schemas import CamelCaseModel, ReviewJob
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES
from app.application.ports import UnitOfWork
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)

# Задачи на реализацию ручного запуска пока нет — ссылка появится в описании операции.
_CREATE_REVIEW_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд "
    "генерировал типы; план ручного запуска — PIPELINE_SPEC.md §4. Сейчас ревью запускает "
    "только вебхук. Когда ручка заработает: 202 с ReviewJob, 409 REVIEW_ALREADY_ACTIVE, "
    "если по этому коммиту прогон уже идёт."
)

# Сессия — на весь роутер: исключения видны здесь, а не в middleware.
router = APIRouter(
    prefix="/api/v1/reviews",
    tags=["reviews"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


class CreateReviewRequest(CamelCaseModel):
    """Запрос ручного запуска ревью запроса на изменение."""

    model_config = ConfigDict(extra="forbid")

    provider: Provider
    provider_repository_id: str = Field(
        description="Идентификатор репозитория у провайдера (repositories.provider_id).",
        examples=["123456789"],
    )
    pull_request_number: int = Field(ge=1, examples=[123])
    trigger: TriggerSource


class FindingResponse(BaseModel):
    id: UUID
    file_path: str
    side: DiffSide
    old_line: int | None
    new_line: int | None
    category: FindingCategory
    severity: FindingSeverity
    message: str
    suggestion: str | None
    confidence: float | None


class ReviewRunResponse(BaseModel):
    id: UUID
    merge_request_id: UUID
    head_sha: str
    base_sha: str | None
    status: ReviewRunStatus
    trigger: TriggerSource
    model: str | None
    tokens_used: int | None
    duration_seconds: float | None
    failure_reason: str | None
    failed_stage: ReviewRunStatus | None = Field(
        description="Этап, на котором прогон упал (building_context, analysing, publishing); null, если не падал."
    )
    last_progress_at: datetime
    created_at: datetime
    updated_at: datetime
    findings: list[FindingResponse]


@router.post(
    "",
    status_code=202,
    response_model=ReviewJob,
    summary="Запустить ревью вручную",
    response_description="ReviewJob создан и поставлен в очередь.",
    description=_CREATE_REVIEW_DESCRIPTION,
    responses={**ERRORS_403_ORIGIN, **ERRORS_409_REVIEW_ACTIVE, **ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def create_review(body: CreateReviewRequest) -> ReviewJob:
    raise ApiError(501, ErrorCode.NOT_IMPLEMENTED, "Ручной запуск ревью ещё не реализован.")


@router.get(
    "/{review_run_id}",
    response_model=ReviewRunResponse,
    summary="Прогон ревью вместе с находками",
    response_description="Прогон и его находки.",
    description=(
        "Находки — в стабильном порядке: по времени создания, затем по id. Прогон без находок — "
        "200 с пустым findings."
    ),
    responses={**ERRORS_404_REVIEW, **ERRORS_422},
)
def get_review(
    review_run_id: UUID,
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> ReviewRunResponse:
    run = uow.review_runs.get(review_run_id)
    if run is None:
        raise ApiError(404, ErrorCode.REVIEW_NOT_FOUND, "Прогона ревью с таким id нет.")

    findings = uow.findings.list_for_run(review_run_id)
    return ReviewRunResponse(
        id=run.id,
        merge_request_id=run.merge_request_id,
        head_sha=run.head_sha,
        base_sha=run.base_sha,
        status=run.status,
        trigger=run.trigger,
        model=run.model,
        tokens_used=run.tokens_used,
        duration_seconds=run.duration_seconds,
        failure_reason=run.failure_reason,
        failed_stage=run.failed_stage,
        last_progress_at=run.last_progress_at,
        created_at=run.created_at,
        updated_at=run.updated_at,
        findings=[
            FindingResponse(
                id=f.id,
                file_path=f.anchor.file_path,
                side=f.anchor.side,
                old_line=f.anchor.old_line,
                new_line=f.anchor.new_line,
                category=f.category,
                severity=f.severity,
                message=f.message,
                suggestion=f.suggestion,
                confidence=f.confidence,
            )
            for f in findings
        ],
    )
