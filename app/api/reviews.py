"""Прогон ревью: чтение вместе с находками и ручной запуск (пока заглушка).

Один связный ответ, не два эндпоинта: прогон без находок бессмысленно
смотреть отдельно от них (design.md change'а add-review-rest-api).

`ReviewJob` — публичная проекция прогона в camelCase: её отдают ручной запуск и
приём вебхука (`app/api/webhooks.py`).
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

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


class _CamelCase(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)


class CreateReviewRequest(_CamelCase):
    """Запрос ручного запуска ревью запроса на изменение."""

    model_config = ConfigDict(extra="forbid")

    provider: Provider
    provider_repository_id: str = Field(
        description="Идентификатор репозитория у провайдера (repositories.provider_id).",
        examples=["123456789"],
    )
    pull_request_number: int = Field(ge=1, examples=[123])
    trigger: TriggerSource


ReviewErrorCode = Literal[
    "SCM_AUTH_ERROR",
    "PR_NOT_FOUND",
    "REPOSITORY_NOT_FOUND",
    "SCM_UNAVAILABLE",
    "CONTEXT_PARSING_ERROR",
    "LLM_RATE_LIMITED",
    "LLM_TIMEOUT",
    "LLM_UNAVAILABLE",
    "LLM_INVALID_OUTPUT",
    "REVIEW_RUN_STALE",
    "INTERNAL_ERROR",
]


class ReviewError(_CamelCase):
    """Структурированная ошибка прогона (§8 PIPELINE_SPEC.md) — не ошибка HTTP-запроса.

    Коды SCM_* провайдер-нейтральны: второй провайдер не ломает контракт.
    """

    code: ReviewErrorCode
    message: str
    retryable: bool
    scm_provider: Provider | None = Field(
        description=(
            "Заполнено для SCM_AUTH_ERROR/SCM_UNAVAILABLE, иначе null. Называется scmProvider, "
            "а не provider: поле про сторону SCM, providerAttempt считает попытки и LLM, и SCM."
        )
    )
    provider_attempt: int | None = Field(
        ge=1,
        description="Попытка вызова LLM/SCM провайдера. Не путать с redelivery сообщения в очереди.",
    )


class ReviewJob(_CamelCase):
    """Публичная проекция прогона ревью (review_runs) в camelCase.

    Не путать с сообщением очереди: у него нет собственной идентичности, отдельной от id прогона.
    """

    id: str = Field(description="Идентификатор прогона (reviewRunId).")
    provider: Provider
    provider_repository_id: str
    pull_request_number: int = Field(ge=1)
    trigger: TriggerSource
    head_commit_sha: str | None = None
    base_commit_sha: str | None = None
    status: ReviewRunStatus
    findings_count: int = Field(ge=0)
    rejected_findings: int = Field(ge=0)
    model: str | None = None
    tokens_used: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    created_at: datetime
    updated_at: datetime
    last_progress_at: datetime = Field(
        description="Обновляется на каждом переходе состояния; по нему находят зависшие прогоны (§7.5 PIPELINE_SPEC.md)."
    )
    failed_stage: ReviewRunStatus | None = Field(
        default=None,
        description="Этап, на котором прогон упал (building_context, analysing, publishing); null, если не падал.",
    )
    error: ReviewError | None


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
