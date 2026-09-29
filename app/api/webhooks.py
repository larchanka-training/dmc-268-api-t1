"""Приём вебхуков GitHub.

Транспорт: разбор запроса, проверка подписи, вызов портов. Что считать
дублем и когда переходить в failed — решает `run_review`
(`app/application/review_pipeline.py`) в воркере, а не этот роутер; здесь —
только создание прогона и постановка задачи.
"""

import hashlib
import hmac
import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.dependencies import get_job_queue, get_settings, get_unit_of_work
from app.application.ports import UnitOfWork
from app.application.ports.job_queue import JobQueue
from app.config import Settings
from app.domain.entities import MergeRequest, Repository, ReviewJob, ReviewRun
from app.domain.enums import MergeRequestState, Provider, ReviewRunStatus, TriggerSource
from app.domain.ids import new_id

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


def _signature_is_valid(secret: str, body: bytes, header_value: str | None) -> bool:
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value)


def _upsert_repository(uow: UnitOfWork, repo_payload: dict[str, Any], now: datetime) -> Repository:
    provider_id = str(repo_payload["id"])
    repository = uow.repositories.find_by_provider(Provider.GITHUB, provider_id)
    if repository is not None:
        return repository
    repository = Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id=provider_id,
        full_name=repo_payload["full_name"],
        default_branch=repo_payload.get("default_branch", "main"),
        # Регистрация с явной проверкой прав — отдельный, не начатый шов
        # (BACKEND_ARCHITECTURE.md, threat model). До него репозиторий,
        # впервые увиденный по вебхуку, ревьюится по умолчанию.
        auto_review_enabled=True,
        created_at=now,
        updated_at=now,
    )
    uow.repositories.add(repository)
    return repository


def _upsert_merge_request(
    uow: UnitOfWork, repository: Repository, pr_payload: dict[str, Any], head_sha: str, now: datetime
) -> MergeRequest:
    number = pr_payload["number"]
    existing = uow.merge_requests.find_by_number(repository.id, number)
    if existing is None:
        merge_request = MergeRequest(
            id=new_id(),
            repository_id=repository.id,
            number=number,
            title=pr_payload.get("title", ""),
            description=pr_payload.get("body") or "",
            author=pr_payload.get("user", {}).get("login", ""),
            source_branch=pr_payload.get("head", {}).get("ref", ""),
            target_branch=pr_payload.get("base", {}).get("ref", ""),
            head_sha=head_sha,
            state=MergeRequestState.OPEN,
            created_at=now,
            updated_at=now,
        )
        uow.merge_requests.add(merge_request)
        return merge_request
    updated = replace(existing, head_sha=head_sha, updated_at=now)
    uow.merge_requests.update(updated)
    return updated


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
    if not _signature_is_valid(settings.github_webhook_secret, body, x_hub_signature_256):
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

    now = datetime.now(UTC)
    head_sha = pr_payload["head"]["sha"]
    base_sha = pr_payload["base"]["sha"]

    repository = _upsert_repository(uow, repo_payload, now)
    merge_request = _upsert_merge_request(uow, repository, pr_payload, head_sha, now)

    if uow.review_runs.find_active(merge_request.id, head_sha) is not None:
        uow.commit()
        return {"status": "accepted"}

    run_id = new_id()
    uow.review_runs.add(
        ReviewRun(
            id=run_id,
            merge_request_id=merge_request.id,
            head_sha=head_sha,
            base_sha=base_sha,
            status=ReviewRunStatus.QUEUED,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=now,
            created_at=now,
            updated_at=now,
        )
    )

    # Постановка в очередь — до commit. Если брокер недоступен, исключение
    # откатывает и ReviewRun: доставка не остаётся молча потерянной записью
    # в статусе queued, которую find_active потом примет за уже взятую в
    # работу — GitHub получит 5xx и повторит вебхук с нуля.
    job_queue.enqueue(
        ReviewJob(
            id=run_id,
            event_type=x_github_event,
            action=payload.get("action", ""),
            repository_provider_id=repository.provider_id,
            repository_full_name=repository.full_name,
            pull_request_number=pr_payload["number"],
            head_sha=head_sha,
            base_sha=base_sha,
        )
    )
    uow.commit()
    return {"status": "accepted"}
