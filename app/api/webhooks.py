"""Эндпоинт приёма вебхуков.

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
`{"status": "ignored"}` по спеке вебхуков. Ошибки — `ApiError`, тело —
Problem Details из `app/api/errors.py`.

Тело читается из `Request`, а не параметром `Body`: FastAPI для JSON-типа
контента разбирает тело до проверки типов, что нарушило бы сценарий «подпись
проверяется до разбора payload'а». Тяжёлая работа (VCS, база, брокер) уходит
в threadpool, чтобы не блокировать event loop.

GitLab приёмником пока не поддержан: маршрут контрактный уже сейчас, а до
своего адаптера провайдер отвечает 501.
"""

import json
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_container
from app.api.errors import (
    ERRORS_401_WEBHOOK_SIGNATURE,
    ERRORS_422,
    ERRORS_501_WEBHOOK_PROVIDER,
    ERRORS_502_SCM,
    ApiError,
)
from app.api.problems import ErrorCode
from app.api.schemas import ReviewJob
from app.application.sync_thread_resolution import handle_review_thread_webhook
from app.application.use_cases.handle_webhook import (
    WebhookOutcome,
    handle_webhook_event,
)
from app.domain.enums import Provider
from app.domain.hmac import verify_hmac
from app.domain.ids import new_id
from app.domain.vcs_errors import VcsError

# Путь контракта — /api/v1/webhooks/{provider}; префикс ставит роутер,
# как и остальные роутеры приложения.
router = APIRouter(prefix="/api/v1")

# Приём вебхуков поддержан ровно для адаптеров, которые уже написаны.
SUPPORTED_PROVIDERS = frozenset({Provider.GITHUB})

# Проекцию прогона отдают все исходы с созданной записью: и созданный,
# и найденный существующий (повторная доставка, проигравший гонку) —
# для вызывающего это один исход «ReviewJob создан или найден существующий».
_JOB_OUTCOMES = ("success", "duplicate", "conflict")

# Тип события GitHub присылает заголовком; прогон создаёт ровно `pull_request`.
EVENT_HEADER = "X-GitHub-Event"
TRIGGERING_EVENT = "pull_request"
REVIEW_THREAD_EVENT = "pull_request_review_thread"



class WebhookIgnored(BaseModel):
    """Тело `202` для доставки, которая ревью не запускает: действие reopened, closed или
    неизвестное, незарегистрированный репозиторий, тело без события."""

    status: Literal["ignored"]


class WebhookAcknowledged(BaseModel):
    """Тело `202` для события pull_request_review_thread: отметка закрытия треда принята."""

    status: Literal["acknowledged"]


@router.post(
    "/webhooks/{provider}",
    status_code=202,
    # Ручка отдаёт `JSONResponse` сама; модель — для схемы, тело сверяют тесты.
    response_model=ReviewJob | WebhookIgnored | WebhookAcknowledged,
    tags=["webhooks"],
    # Без сессии: доставку подтверждает подпись.
    openapi_extra={"security": []},
    response_description=(
        "Доставка принята: ReviewJob создан или найден существующий; WebhookIgnored — доставка "
        "ревью не запускает; WebhookAcknowledged — событие pull_request_review_thread."
    ),
    summary="Принять webhook-доставку от хостинга и поставить ревью в очередь",
    description=(
        "Без сессии и без проверки Origin: доставку подтверждает подпись. ReviewJob создан или, "
        "если для этого коммита уже есть нетерминальный, возвращён существующий. Дубль — не "
        "ошибка: не-2xx хостинг считает неудачной доставкой и повторяет её, поэтому 409 здесь "
        "не отдаём."
    ),
    responses={
        **ERRORS_401_WEBHOOK_SIGNATURE,
        **ERRORS_422,
        **ERRORS_501_WEBHOOK_PROVIDER,
        **ERRORS_502_SCM,
    },
)
async def receive_webhook(provider: Provider, request: Request) -> Response:
    if provider not in SUPPORTED_PROVIDERS:
        raise ApiError(
            501,
            ErrorCode.PROVIDER_NOT_SUPPORTED,
            "Приёмник вебхуков для этого провайдера не реализован.",
        )
    container = get_container(request)
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    secret = container.settings.github_webhook_secret.get_secret_value().encode("utf-8")
    # Пустой секрет не подписывает ничего: все вебхуки отклоняются, в том
    # числе «подписанные» пустым ключом.
    if not secret or signature is None or not verify_hmac(body, signature, secret):
        raise ApiError(
            401,
            ErrorCode.WEBHOOK_SIGNATURE_INVALID,
            "Подписи X-Hub-Signature-256 нет, или она не совпала с телом доставки.",
        )

    event_type = request.headers.get(EVENT_HEADER)

    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError:
        # Подпись валидна, но тело — не JSON: события из него не извлечь,
        # поэтому это игнор, а не ошибка.
        return JSONResponse(status_code=202, content={"status": "ignored"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=202, content={"status": "ignored"})

    if event_type == REVIEW_THREAD_EVENT:
        try:
            await run_in_threadpool(
                handle_review_thread_webhook,
                payload,
                uow=container.unit_of_work(),
                vcs=container.vcs_gateway(),
                now=lambda: datetime.now(UTC),
            )
        except VcsError:
            raise _scm_unavailable()
        return JSONResponse(status_code=202, content={"status": "acknowledged"})

    # Гейт события стоит до разбора тела: подписанная доставка другого события
    # с pull-request-совместимой формой (`pull_request_target`) не должна ни
    # создавать записи, ни доходить до use case.
    if event_type != TRIGGERING_EVENT:
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
        raise _scm_unavailable()
    if outcome.kind == "ignored":
        return JSONResponse(status_code=202, content={"status": "ignored"})
    if outcome.kind in _JOB_OUTCOMES:
        return JSONResponse(status_code=202, content=_review_job_payload(outcome))
    raise RuntimeError(f"неожиданный исход use case: {outcome.kind!r}")


def _scm_unavailable() -> ApiError:
    return ApiError(
        502,
        ErrorCode.SCM_UNAVAILABLE,
        "Провайдер VCS не ответил или ответил ошибкой; прогон не создан.",
    )


def _review_job_payload(outcome: WebhookOutcome) -> dict[str, Any]:
    """Проекция прогона в публичную схему `ReviewJob` (`app/api/schemas.py`).

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
