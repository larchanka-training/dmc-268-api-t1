"""Эндпоинт приёма вебхуков GitHub.

Порядок строго по спеке: сырое тело читается до любого разбора, подпись из
заголовка `X-Hub-Signature-256` проверяется чистой функцией `verify_hmac` — и
только после этого payload разбирается и уходит в use case. Без подписи или с
неверной — 401, ни одной записи; ошибка VCS — 502; игнор (`reopened`,
незарегистрированный репозиторий, неизвестное действие) и созданный прогон —
единый код 202 (решение владельца продукта, design D4).

Тело читается из `Request`, а не параметром `Body`: FastAPI для JSON-типа
контента разбирает тело до проверки типов, что нарушило бы сценарий «подпись
проверяется до разбора payload'а». Тяжёлая работа (VCS, база, брокер) уходит
в threadpool, чтобы не блокировать event loop.
"""

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_container
from app.application.use_cases.handle_webhook import handle_webhook_event
from app.domain.hmac import verify_hmac
from app.domain.ids import new_id

router = APIRouter()


@router.post("/webhooks/github", status_code=202)
async def handle_github_webhook(request: Request) -> dict[str, str]:
    container = get_container(request)
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    secret = container.settings.github_webhook_secret.encode("utf-8")
    # Пустой секрет не подписывает ничего: все вебхуки отклоняются, в том
    # числе «подписанные» пустым ключом.
    if not secret or signature is None or not verify_hmac(body, signature, secret):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError:
        # Подпись валидна, но тело — не JSON: события из него не извлечь,
        # поэтому это игнор, а не ошибка.
        return {"status": "ignored"}
    if not isinstance(payload, dict):
        return {"status": "ignored"}

    outcome = await run_in_threadpool(
        handle_webhook_event,
        payload,
        container.unit_of_work(),
        container.vcs_gateway(),
        container.job_queue(),
        now=lambda: datetime.now(UTC),
        new_id=new_id,
    )
    if outcome.kind == "failure":
        raise HTTPException(status_code=502, detail="VCS provider unavailable")
    if outcome.kind == "ignored":
        return {"status": "ignored"}
    return {"status": "created", "review_run_id": str(outcome.review_run_id)}
