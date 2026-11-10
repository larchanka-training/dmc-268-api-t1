"""Инспектор находки: почему модель это сказала.

Правило и уверенность находки уже есть в `FindingResponse`; здесь - то, что нужно только
детальному виду: вердикт верификатора и контекст модели по файлу находки (#289, #290).
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
    ERRORS_502_SCM_VISIBILITY,
    ApiError,
)
from app.api.problems import ErrorCode
from app.api.reviews import visible_run
from app.api.schemas import (
    FileContextResponse,
    VerifierVerdictResponse,
    file_context_response,
    verifier_verdict_response,
)
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES
from app.application.ports import UnitOfWork
from app.domain.entities import ReviewRun
from app.domain.enums import VerificationOutcome
from app.domain.file_context import file_context_of

_INSPECTION_DESCRIPTION = (
    "Требует сессию. Прогон проверяется по тому же правилу, что у GET /reviews/{review_run_id}: "
    "404 REVIEW_NOT_FOUND, если его нет или он не виден пользователю; затем находка - "
    "404 FINDING_NOT_FOUND, если у прогона её нет. Отдаёт итог второго прохода прогона, вердикт "
    "верификатора по находке и контекст, который модель видела по файлу находки. Правило и уверенность - "
    "в самой находке (FindingResponse). Контекст отдаётся таким, каким сохранён: с секретами, заменёнными "
    "меткой; у прогонов, чей пакет контекста не сохранён или стёрт по сроку хранения, context - null. "
    "verdict - null, если верификатор по находке не решал или прогон записан до сохранения вердиктов."
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
        **ERRORS_502_SCM_VISIBILITY,
    },
)
def get_finding_inspection(
    finding_id: Annotated[UUID, Path(description="Идентификатор находки прогона.")],
    run: ReviewRun = Depends(visible_run),
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> FindingInspectionResponse:
    finding = next((f for f in uow.findings.list_for_run(run.id) if f.id == finding_id), None)
    if finding is None:
        raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.FINDING_NOT_FOUND, "У прогона ревью нет находки с таким id.")
    bodies = [payload.body for payload in uow.context_payloads.list_for_run(run.id)]
    context = file_context_of(bodies, finding.anchor.file_path)
    return FindingInspectionResponse(
        finding_id=finding.id,
        verification=run.verification,
        verdict=verifier_verdict_response(finding.verdict),
        context=None if context is None else file_context_response(context),
    )
