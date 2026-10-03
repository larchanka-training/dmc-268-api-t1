"""Эндпоинт приёма вебхуков по контракту `docs/openapi/openapi.yaml`.

Порядок строго по спеке: сырое тело читается до любого разбора, подпись из
заголовка `X-Hub-Signature-256` проверяется чистой функцией `verify_hmac`,
затем заголовок `X-GitHub-Event` отсекает чужие события — и только после
этого payload разбирается и уходит в use case. Без подписи или с
неверной — 401, ни одной записи; подписанное событие не `pull_request`
— 202 `{"status": "ignored"}` без разбора тела: у GitHub есть события вроде
`pull_request_target` с неотличимой по форме полезной нагрузкой, поэтому тип
события берётся из заголовка, а не из полей тела; ошибка VCS — 502;
повторная доставка и
проигравшая гонку доставка — 202 с проекцией существующего прогона: дубль для
хостинга не ошибка (контракт: «ReviewJob создан или найден существующий»), а
не-2xx он считает неудачной доставкой и повторяет её; игнор (`reopened`,
незарегистрированный репозиторий, неизвестное действие) — 202
`{"status": "ignored"}` по спеке вебхуков. Тела ошибок — `ApiError`
(`code` + `message`), как в контракте, поэтому без `HTTPException` с его
`{"detail": …}`.

Тело читается из `Request`, а не параметром `Body`: FastAPI для JSON-типа
контента разбирает тело до проверки типов, что нарушило бы сценарий «подпись
проверяется до разбора payload'а». Тяжёлая работа (VCS, база, брокер) уходит
в threadpool, чтобы не блокировать event loop.

GitLab приёмником пока не поддержан: маршрут контрактный уже сейчас, а до
своего адаптера провайдер отвечает 501.
"""

import json
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_container
from app.application.use_cases.handle_webhook import (
    WebhookOutcome,
    handle_webhook_event,
)
from app.domain.enums import Provider
from app.domain.hmac import verify_hmac
from app.domain.ids import new_id

# Путь контракта — /api/v1/webhooks/{provider}; префикс ставит само приложение.
ROUTER_PREFIX = "/api/v1"

router = APIRouter()

# Приём вебхуков поддержан ровно для адаптеров, которые уже написаны.
SUPPORTED_PROVIDERS = frozenset({Provider.GITHUB})

# Проекцию прогона отдают все исходы с созданной записью: и созданный,
# и найденный существующий (повторная доставка, проигравший гонку) —
# для вызывающего это один исход «ReviewJob создан или найден существующий».
_JOB_OUTCOMES = ("success", "duplicate", "conflict")

# Тип события GitHub присылает заголовком; прогон создаёт ровно `pull_request`.
EVENT_HEADER = "X-GitHub-Event"
TRIGGERING_EVENT = "pull_request"


def _api_error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, "message": message})


@router.post("/webhooks/{provider}", status_code=202)
async def receive_webhook(provider: Provider, request: Request) -> Response:
    if provider not in SUPPORTED_PROVIDERS:
        return _api_error(
            501,
            "PROVIDER_NOT_SUPPORTED",
            f"провайдер {provider.value} приёмником вебхуков не поддержан",
        )
    container = get_container(request)
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    secret = container.settings.github_webhook_secret.get_secret_value().encode("utf-8")
    # Пустой секрет не подписывает ничего: все вебхуки отклоняются, в том
    # числе «подписанные» пустым ключом.
    if not secret or signature is None or not verify_hmac(body, signature, secret):
        return _api_error(401, "WEBHOOK_SIGNATURE_INVALID", "invalid webhook signature")

    # Гейт события стоит до разбора тела: подписанная доставка другого события
    # с pull-request-совместимой формой (`pull_request_target`) не должна ни
    # создавать записи, ни доходить до use case.
    if request.headers.get(EVENT_HEADER) != TRIGGERING_EVENT:
        return JSONResponse(status_code=202, content={"status": "ignored"})

    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError:
        # Подпись валидна, но тело — не JSON: события из него не извлечь,
        # поэтому это игнор, а не ошибка.
        return JSONResponse(status_code=202, content={"status": "ignored"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=202, content={"status": "ignored"})

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
        return _api_error(502, "SCM_UNAVAILABLE", "VCS provider unavailable")
    if outcome.kind == "ignored":
        return JSONResponse(status_code=202, content={"status": "ignored"})
    if outcome.kind in _JOB_OUTCOMES:
        return JSONResponse(status_code=202, content=_review_job_payload(outcome))
    raise RuntimeError(f"неожиданный исход use case: {outcome.kind!r}")


def _review_job_payload(outcome: WebhookOutcome) -> dict[str, Any]:
    """Проекция прогона в публичную схему `ReviewJob` из `openapi.yaml`.

    `findingsCount` у свежего прогона всегда 0: находки появляются позже,
    при обработке задачи воркером, и в этот эндпоинт больше не возвращаются.
    """
    run, merge_request, repository = outcome.run, outcome.merge_request, outcome.repository
    if run is None or merge_request is None or repository is None:
        raise RuntimeError(f"исход {outcome.kind!r} без записей для проекции ReviewJob")
    return {
        "id": str(run.id),
        "provider": repository.provider.value,
        "providerRepositoryId": repository.provider_id,
        "pullRequestNumber": merge_request.number,
        "trigger": run.trigger.value,
        "status": run.status.value,
        "headCommitSha": run.head_sha,
        "baseCommitSha": run.base_sha,
        "findingsCount": 0,
        "rejectedFindings": run.rejected_findings,
        "createdAt": run.created_at.isoformat(),
        "updatedAt": run.updated_at.isoformat(),
        "lastProgressAt": run.last_progress_at.isoformat(),
        "error": None,
    }
