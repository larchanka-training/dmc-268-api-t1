"""Прогон ревью: чтение вместе с находками и ручной запуск (пока заглушка).

Один связный ответ, не два эндпоинта: прогон без находок бессмысленно
смотреть отдельно от них (design.md change'а add-review-rest-api).

`ReviewJob` — общая модель ответа ручного запуска и приёма вебхука, лежит в
`app/api/schemas.py`.
"""

from dataclasses import asdict
from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

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
from app.api.schemas import (
    FAILED_STAGE_DESCRIPTION,
    CamelCaseRequest,
    FailedStage,
    ReviewJob,
    UtcDatetime,
)
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
    "если по этому коммиту прогон уже идёт. Требует сессию и Origin сервиса; Origin "
    "проверяется раньше сессии и разбора тела."
)

# Сессия — на весь роутер: исключения видны здесь, а не в middleware.
router = APIRouter(
    prefix="/api/v1/reviews",
    tags=["reviews"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


class CreateReviewRequest(CamelCaseRequest):
    """Запрос ручного запуска ревью запроса на изменение."""

    # strict у модели не пропустил бы enum строкой: тело приходит разобранным JSON, а в
    # Python-режиме strict ждёт экземпляр enum. Для enum-полей — точечно строкой из списка.
    provider: Provider = Field(strict=False, description="Хостинг репозитория.", examples=["github"])
    provider_repository_id: str = Field(
        min_length=1,
        max_length=255,
        description="Идентификатор репозитория у провайдера (repositories.provider_id).",
        examples=["123456789"],
    )
    pull_request_number: int = Field(
        ge=1, le=2_147_483_647, description="Номер запроса на изменение у провайдера.", examples=[123]
    )
    trigger: TriggerSource = Field(strict=False, description="Источник запуска.", examples=["manual"])


class FindingResponse(BaseModel):
    """Находка ревью, привязанная к строке диффа."""

    id: UUID
    file_path: str = Field(description="Путь файла от корня репозитория.", examples=["app/main.py"])
    side: DiffSide = Field(description="Сторона диффа, к которой привязана находка.", examples=["new"])
    old_line: int | None = Field(description="Строка в старой версии файла, с 1; null, если находка на новой стороне.")
    new_line: int | None = Field(description="Строка в новой версии файла, с 1; null, если находка на старой стороне.")
    category: FindingCategory = Field(description="Ось ревью, к которой относится находка.", examples=["security"])
    severity: FindingSeverity = Field(description="Насколько находка важна.", examples=["high"])
    message: str = Field(description="Текст замечания.")
    suggestion: str | None = Field(description="Предлагаемая правка; null, если её нет.")
    confidence: float | None = Field(
        description="Уверенность модели в находке, обычно от 0 до 1; null, если модель её не дала."
    )


class ReviewRunResponse(BaseModel):
    """Прогон ревью вместе с его находками."""

    id: UUID
    merge_request_id: UUID = Field(description="Запрос на изменение, к которому относится прогон.")
    head_sha: str = Field(
        description="Коммит, который ревьюится.", examples=["a1b2c3d4e5f6789012345678abcdef0123456789"]
    )
    base_sha: str | None = Field(
        description="Коммит, относительно которого считается дифф; null, пока воркер его не узнал.",
        examples=["fedcba0987654321fedcba0987654321fedcba09"],
    )
    status: ReviewRunStatus = Field(description="Текущий статус прогона.", examples=["completed"])
    trigger: TriggerSource = Field(description="Что запустило прогон.", examples=["webhook"])
    model: str | None = Field(description="Модель LLM, которая провела ревью; null до анализа.")
    tokens_used: int | None = Field(description="Токенов LLM потрачено; null до анализа.")
    duration_seconds: float | None = Field(description="Длительность прогона в секундах; null, пока не завершён.")
    failure_reason: str | None = Field(description="Почему прогон упал; null, если не падал.")
    failed_stage: FailedStage | None = Field(description=FAILED_STAGE_DESCRIPTION)
    last_progress_at: UtcDatetime = Field(description="Время последнего перехода состояния.")
    created_at: UtcDatetime = Field(description="Когда прогон создан.")
    updated_at: UtcDatetime = Field(description="Когда запись последний раз менялась.")
    findings: list[FindingResponse] = Field(description="Находки прогона: по времени создания, затем по id.")


@router.post(
    "",
    status_code=HTTPStatus.ACCEPTED,
    response_model=ReviewJob,
    summary="Запустить ревью вручную",
    response_description="ReviewJob создан и поставлен в очередь.",
    description=_CREATE_REVIEW_DESCRIPTION,
    responses={**ERRORS_403_ORIGIN, **ERRORS_409_REVIEW_ACTIVE, **ERRORS_422, **ERRORS_501_NOT_IMPLEMENTED},
)
def create_review(body: CreateReviewRequest) -> ReviewJob:
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Ручной запуск ревью ещё не реализован.")


@router.get(
    "/{review_run_id}",
    response_model=ReviewRunResponse,
    summary="Прогон ревью вместе с находками",
    response_description="Прогон и его находки.",
    description=(
        "Требует сессию. Находки — в стабильном порядке: по времени создания, затем по id. Прогон без находок — "
        "200 с пустым findings."
    ),
    responses={**ERRORS_404_REVIEW, **ERRORS_422},
)
def get_review(
    review_run_id: Annotated[UUID, Path(description="Идентификатор прогона ревью.")],
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> ReviewRunResponse:
    run = uow.review_runs.get(review_run_id)
    if run is None:
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.REVIEW_NOT_FOUND, "Прогона ревью с таким id нет.")

    findings = uow.findings.list_for_run(review_run_id)
    return ReviewRunResponse.model_validate(
        {**asdict(run), "findings": [{**asdict(f), **asdict(f.anchor)} for f in findings]}
    )
