"""Чтение прогона ревью вместе с его находками.

Один связный ответ, не два эндпоинта: прогон без находок бессмысленно
смотреть отдельно от них (design.md change'а add-review-rest-api).
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.dependencies import get_unit_of_work
from app.application.ports import UnitOfWork

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])


class FindingResponse(BaseModel):
    id: UUID
    file_path: str
    side: str
    old_line: int | None
    new_line: int | None
    category: str
    severity: str
    message: str
    suggestion: str | None
    confidence: float | None


class ReviewRunResponse(BaseModel):
    id: UUID
    merge_request_id: UUID
    head_sha: str
    base_sha: str | None
    status: str
    trigger: str
    model: str | None
    tokens_used: int | None
    duration_seconds: float | None
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime
    findings: list[FindingResponse]


@router.get("/{review_run_id}", response_model=ReviewRunResponse)
def get_review(
    review_run_id: UUID,
    uow: UnitOfWork = Depends(get_unit_of_work),
) -> ReviewRunResponse:
    run = uow.review_runs.get(review_run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="review run not found")

    findings = uow.findings.list_for_run(review_run_id)
    return ReviewRunResponse(
        id=run.id,
        merge_request_id=run.merge_request_id,
        head_sha=run.head_sha,
        base_sha=run.base_sha,
        status=run.status.value,
        trigger=run.trigger.value,
        model=run.model,
        tokens_used=run.tokens_used,
        duration_seconds=run.duration_seconds,
        failure_reason=run.failure_reason,
        created_at=run.created_at,
        updated_at=run.updated_at,
        findings=[
            FindingResponse(
                id=f.id,
                file_path=f.anchor.file_path,
                side=f.anchor.side.value,
                old_line=f.anchor.old_line,
                new_line=f.anchor.new_line,
                category=f.category.value,
                severity=f.severity.value,
                message=f.message,
                suggestion=f.suggestion,
                confidence=f.confidence,
            )
            for f in findings
        ],
    )
