"""Приём вебхуков GitHub.

Транспорт: разбор запроса, проверка подписи, вызов портов. Что считать
дублем и когда переходить в failed — решает `run_review`
(`app/application/review_pipeline.py`) в воркере, а не этот роутер; здесь —
только создание прогона и постановка задачи.
"""

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.api.dependencies import get_job_queue, get_settings, get_unit_of_work
from app.application.ports import UnitOfWork
from app.application.ports.job_queue import JobQueue
from app.config import Settings
from app.domain.entities import MergeRequest, Repository, ReviewJob, ReviewRun
from app.domain.enums import (
    TERMINAL_STATUSES,
    MergeRequestState,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.domain.ids import new_id
from app.domain.lifecycle import advance
from app.domain.webhook_signature import verify_webhook_signature

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


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


def _merge_request_state(pr_payload: dict[str, Any]) -> MergeRequestState:
    # GitHub присылает `state` open/closed и отдельный флаг `merged`.
    if pr_payload.get("merged"):
        return MergeRequestState.MERGED
    if pr_payload.get("state") == "closed":
        return MergeRequestState.CLOSED
    return MergeRequestState.OPEN


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
            state=_merge_request_state(pr_payload),
            created_at=now,
            updated_at=now,
        )
        uow.merge_requests.add(merge_request)
        return merge_request
    updated = replace(
        existing,
        title=pr_payload.get("title", existing.title),
        description=pr_payload.get("body") or existing.description,
        source_branch=pr_payload.get("head", {}).get("ref", existing.source_branch),
        target_branch=pr_payload.get("base", {}).get("ref", existing.target_branch),
        head_sha=head_sha,
        state=_merge_request_state(pr_payload),
        updated_at=now,
    )
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

    # Сначала commit, потом enqueue. Обратный порядок отдаёт сообщение воркеру
    # раньше, чем строка прогона видна в базе: воркер не находит прогон,
    # подтверждает сообщение, а API затем коммитит `queued` — задача потеряна,
    # и `find_active` блокирует коммит на каждую следующую доставку.
    uow.commit()

    try:
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
    except Exception as exc:
        # «Закоммитили, но не поставили»: без компенсации прогон навсегда
        # остался бы `queued`, а повторная доставка GitHub получила бы 202 от
        # `find_active` и ничего не поставила. Ошибка идёт дальше — GitHub
        # получит 5xx и повторит вебхук. Если не удалась и компенсация (база
        # недоступна), прогон выметет `sweep_stale_runs` в воркере.
        _fail_unenqueued(uow, run_id, exc)
        raise
    return {"status": "accepted"}


def _fail_unenqueued(uow: UnitOfWork, run_id: UUID, cause: Exception) -> None:
    try:
        run = uow.review_runs.get(run_id)
        if run is None or run.status in TERMINAL_STATUSES:
            return
        failed = replace(
            advance(run, ReviewRunStatus.FAILED, datetime.now(UTC)).unwrap(),
            failure_reason=f"не удалось поставить задачу в очередь: {cause}",
        )
        uow.review_runs.update(failed)
        uow.commit()
    except Exception:
        logger.exception("не удалось перевести прогон %s в failed после сбоя очереди", run_id)
