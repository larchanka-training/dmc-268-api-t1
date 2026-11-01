"""Инспектор находки: почему модель это сказала (пока заглушка).

Правило и уверенность находки уже есть в `FindingResponse`; здесь - то, что нужно только
детальному виду: вердикт верификатора и контекст модели по файлу находки (#289). Ручка
описана заранее, чтобы фронтенд генерировал типы (dmc-268-ui-t1#113); реализация - #290.
"""

from http import HTTPStatus
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path
from pydantic import BaseModel, Field

from app.api.dependencies import get_unit_of_work
from app.api.errors import (
    ERRORS_404_REVIEW_OR_FINDING,
    ERRORS_422,
    ERRORS_501_NOT_IMPLEMENTED,
    ERRORS_502_SCM_VISIBILITY,
    ApiError,
)
from app.api.problems import ErrorCode
from app.api.reviews import visible_run
from app.api.schemas import FileContextResponse, VerifierVerdictResponse
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES
from app.application.ports import UnitOfWork
from app.domain.entities import ReviewRun
from app.domain.enums import VerificationOutcome

_INSPECTION_DESCRIPTION = (
    "Не реализовано: отвечает 501 NOT_IMPLEMENTED. Описана заранее, чтобы фронтенд генерировал типы "
    "(задача #290). Требует сессию. Прогон проверяется до ответа 501 по тому же правилу, что у "
    "GET /reviews/{review_run_id}: 404 REVIEW_NOT_FOUND, если его нет или он не виден пользователю; затем "
    "находка - 404 FINDING_NOT_FOUND, если у прогона её нет. Когда ручка заработает: итог второго прохода "
    "прогона, вердикт верификатора по находке и контекст, который модель видела по файлу находки. Правило "
    "и уверенность - в самой находке (FindingResponse). Контекст отдаётся с секретами, заменёнными меткой; "
    "у прогонов, чей пакет контекста не сохранён, context - null."
)

router = APIRouter(
    prefix="/api/v1/reviews",
    tags=["reviews"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


class FindingInspectionResponse(BaseModel):
    """Почему модель выпустила находку: вердикт верификатора и что она видела."""

    finding_id: UUID = Field(description="Находка, которую объясняет ответ.")
    verification: VerificationOutcome | None = Field(
        description="Итог второго прохода модели (Reflection Pass) для прогона; null, если прогон его не записал.",
        examples=["verified"],
    )
    verdict: VerifierVerdictResponse | None = Field(
        description=(
            "Решение верификатора по этой находке; null, если верификатор в прогоне не отработал "
            "(verification не verified) или прогон записан до того, как вердикты стали сохраняться."
        )
    )
    context: FileContextResponse | None = Field(
        description="Что модель видела по файлу находки; null, если пакет контекста прогона не сохранён."
    )


@router.get(
    "/{review_run_id}/findings/{finding_id}/inspection",
    response_model=FindingInspectionResponse,
    summary="Инспекция находки",
    response_description="Вердикт верификатора и контекст модели по файлу находки.",
    description=_INSPECTION_DESCRIPTION,
    responses={
        **ERRORS_404_REVIEW_OR_FINDING,
        **ERRORS_422,
        **ERRORS_501_NOT_IMPLEMENTED,
        **ERRORS_502_SCM_VISIBILITY,
    },
)
def get_finding_inspection(
    finding_id: Annotated[UUID, Path(description="Идентификатор находки прогона.")],
    run: ReviewRun = Depends(visible_run),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> FindingInspectionResponse:
    if all(finding.id != finding_id for finding in uow.findings.list_for_run(run.id)):
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.FINDING_NOT_FOUND, "У прогона ревью нет находки с таким id.")
    raise ApiError(HTTPStatus.NOT_IMPLEMENTED, ErrorCode.NOT_IMPLEMENTED, "Инспекция находки ещё не реализована.")
