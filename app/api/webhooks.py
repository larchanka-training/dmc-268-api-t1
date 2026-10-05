"""Приём вебхуков GitHub.

Транспорт: разбор запроса, проверка подписи, перевод payload GitHub в
`ReviewRequest` и вызов use case `accept_review_request`
(`app/application/review_intake.py`). Регистрация репозитория, создание
прогона и постановка задачи — там; что считать дублем и когда переходить в
failed на стороне воркера — `run_review`.
"""

import json
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.dependencies import get_job_queue, get_settings, get_unit_of_work
from app.application.ports import UnitOfWork
from app.application.ports.job_queue import JobQueue
from app.application.review_intake import ReviewRequest, accept_review_request
from app.config import Settings
from app.domain.enums import MergeRequestState, Provider, TriggerSource
from app.domain.ids import new_id
from app.domain.webhook_signature import verify_webhook_signature

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _merge_request_state(pr_payload: dict[str, Any]) -> MergeRequestState:
    # GitHub присылает `state` open/closed и отдельный флаг `merged`.
    if pr_payload.get("merged"):
        return MergeRequestState.MERGED
    if pr_payload.get("state") == "closed":
        return MergeRequestState.CLOSED
    return MergeRequestState.OPEN


def _to_review_request(
    payload: dict[str, Any],
    pr_payload: dict[str, Any],
    repo_payload: dict[str, Any],
    event_type: str,
) -> ReviewRequest:
    """Перевод payload GitHub в запрос на ревью — транспорт, без решений."""
    return ReviewRequest(
        provider=Provider.GITHUB,
        trigger=TriggerSource.WEBHOOK,
        event_type=event_type,
        action=payload.get("action", ""),
        # GitHub присылает числовой id; в домене он строка (`Repository.provider_id`),
        # чтобы не зависеть от формата идентификаторов конкретного провайдера.
        repository_provider_id=str(repo_payload["id"]),
        repository_full_name=repo_payload["full_name"],
        default_branch=repo_payload.get("default_branch", "main"),
        number=pr_payload["number"],
        title=pr_payload.get("title", ""),
        description=pr_payload.get("body") or "",
        author=pr_payload.get("user", {}).get("login", ""),
        source_branch=pr_payload.get("head", {}).get("ref", ""),
        target_branch=pr_payload.get("base", {}).get("ref", ""),
        head_sha=pr_payload["head"]["sha"],
        base_sha=pr_payload["base"]["sha"],
        state=_merge_request_state(pr_payload),
    )


@router.post("/github", status_code=202)
async def receive_github_webhook(
    request: Request,
    x_hub_signature_256: Annotated[str | None, Header(alias="X-Hub-Signature-256")] = None,
    x_github_event: Annotated[str | None, Header(alias="X-GitHub-Event")] = None,
    settings: Settings = Depends(get_settings),
    uow: UnitOfWork = Depends(get_unit_of_work),
    job_queue: JobQueue = Depends(get_job_queue),
) -> dict[str, str]:
    body = await request.body()
    if not verify_webhook_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        raise HTTPException(status_code=401, detail="invalid signature")

    # Неизвестное/нерелевантное событие — 202, не ошибка: вебхук доставлен и
    # принят, просто нам сейчас нечего с ним делать (docs/testing/TEST_PLAN.md §3.3).
    if x_github_event != "pull_request":
        return {"status": "ignored"}

    payload = json.loads(body)
    pr_payload = payload.get("pull_request")
    repo_payload = payload.get("repository")
    if not pr_payload or not repo_payload:
        return {"status": "ignored"}

    accept_review_request(
        _to_review_request(payload, pr_payload, repo_payload, x_github_event),
        uow=uow,
        job_queue=job_queue,
        now=datetime.now(UTC),
        new_id=new_id,
    )
    return {"status": "accepted"}
