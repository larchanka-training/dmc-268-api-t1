"""Эндпоинт приёма вебхуков.

Порядок строго по спеке: сырое тело читается с лимитом размера до любого разбора и
до подписи (`RequestBodyLimitMiddleware`, `app/api/body_limit.py`: больше лимита — 413
без чтения целиком), подпись из
заголовка `X-Hub-Signature-256` проверяется чистой функцией `verify_hmac`,
затем заголовок `X-GitHub-Event` отсекает чужие события — и только после
этого payload разбирается и уходит в use case. Без подписи или с
неверной — 401, ни одной записи; подписанное событие не `pull_request`
— 202 `{"status": "ignored"}` без разбора тела: у GitHub есть события вроде
`pull_request_target` с неотличимой по форме полезной нагрузкой, поэтому тип
события берётся из заголовка, а не из полей тела; ошибка VCS на метаданных
PR — 502 (дифф приём не запрашивает — его с лимитом размера достаёт воркер);
повторная доставка и проигравшая гонку доставка — 202 с проекцией
существующего прогона: дубль не ошибка (контракт: «ReviewJob создан или найден
существующий»), а не-2xx GitHub записал бы неудавшейся доставкой. Повтор — и
после завершённого не-failed прогона (#75). Сам GitHub неудавшиеся доставки не
повторяет — их переотправляет проход воркера (#149); игнор (`reopened`,
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
from http import HTTPStatus
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Path, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import get_container
from app.api.errors import (
    ERRORS_401_WEBHOOK_SIGNATURE,
    ERRORS_413_BODY,
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

    status: Literal["ignored"] = Field(description="Доставка принята и проигнорирована.")


class WebhookAcknowledged(BaseModel):
    """Тело `202` для события pull_request_review_thread: отметка закрытия треда принята."""

    status: Literal["acknowledged"] = Field(description="Событие треда принято.")


_IGNORED = WebhookIgnored(status="ignored")
_ACKNOWLEDGED = WebhookAcknowledged(status="acknowledged")


@router.post(
    "/webhooks/{provider}",
    status_code=HTTPStatus.ACCEPTED,
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
        "если для этого коммита уже есть нетерминальный или завершённый не-failed прогон по "
        "вебхуку, возвращён существующий. Дубль — не ошибка, поэтому 409 здесь не отдаём: не-2xx "
        "GitHub записывает неудавшейся доставкой, а такие доставки переотправляет воркер. "
        "У VCS приём запрашивает только метаданные PR, их сбой — 502; дифф с лимитом размера "
        "достаёт воркер, его сбой или превышение лимита — failed прогона, а не ответ доставки."
    ),
    responses={
        **ERRORS_401_WEBHOOK_SIGNATURE,
        **ERRORS_413_BODY,
        **ERRORS_422,
        **ERRORS_501_WEBHOOK_PROVIDER,
        **ERRORS_502_SCM,
    },
)
async def receive_webhook(
    provider: Annotated[Provider, Path(description="Хостинг, приславший доставку.")], request: Request
) -> ReviewJob | WebhookIgnored | WebhookAcknowledged:
    if provider not in SUPPORTED_PROVIDERS:
        raise ApiError(
            HTTPStatus.NOT_IMPLEMENTED,
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
            HTTPStatus.UNAUTHORIZED,
            ErrorCode.WEBHOOK_SIGNATURE_INVALID,
            "Подписи X-Hub-Signature-256 нет, или она не совпала с телом доставки.",
        )

    event_type = request.headers.get(EVENT_HEADER)

    try:
        payload: Any = json.loads(body)
    except json.JSONDecodeError:
        # Подпись валидна, но тело — не JSON: события из него не извлечь,
        # поэтому это игнор, а не ошибка.
        return _IGNORED
    if not isinstance(payload, dict):
        return _IGNORED

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
        return _ACKNOWLEDGED

    # Гейт события стоит до разбора тела: подписанная доставка другого события
    # с pull-request-совместимой формой (`pull_request_target`) не должна ни
    # создавать записи, ни доходить до use case.
    if event_type != TRIGGERING_EVENT:
        return _IGNORED

    outcome = await run_in_threadpool(
        handle_webhook_event,
        payload,
        container.unit_of_work(),
        container.vcs_gateway(),
        container.job_queue(),
        now=lambda: datetime.now(UTC),
        new_id=new_id,
        # Компенсация «не поставили в очередь» — терминальный переход прогона:
        # счётчик ведёт адаптер телеметрии (как у пайплайна и выметателя), его
        # выдаёт контейнер — роуты телеметрию не импортируют.
        run_outcome=container.run_outcome(),
    )
    if outcome.kind == "failure":
        raise _scm_unavailable()
    if outcome.kind == "ignored":
        return _IGNORED
    if outcome.kind in _JOB_OUTCOMES:
        return _review_job(outcome)
    raise RuntimeError(f"неожиданный исход use case: {outcome.kind!r}")


def _scm_unavailable() -> ApiError:
    return ApiError(
        HTTPStatus.BAD_GATEWAY,
        ErrorCode.SCM_UNAVAILABLE,
        "Провайдер VCS не ответил или ответил ошибкой; прогон не создан.",
    )


def _review_job(outcome: WebhookOutcome) -> ReviewJob:
    """Проекция прогона в публичную модель `ReviewJob` (`app/api/schemas.py`).

    `findings_count` у свежего прогона всегда 0: находки появляются позже,
    при обработке задачи воркером, и в этот эндпоинт больше не возвращаются.
    """
    run, merge_request, repository = outcome.run, outcome.merge_request, outcome.repository
    if run is None or merge_request is None or repository is None:
        raise RuntimeError(f"исход {outcome.kind!r} без записей для проекции ReviewJob")
    return ReviewJob(
        id=run.id,
        provider=repository.provider,
        provider_repository_id=repository.provider_id,
        pull_request_number=merge_request.number,
        trigger=run.trigger,
        status=run.status,
        head_commit_sha=run.head_sha,
        base_commit_sha=run.base_sha,
        findings_count=0,
        rejected_findings=run.rejected_findings,
        model=run.model,
        tokens_used=run.tokens_used,
        duration_seconds=run.duration_seconds,
        created_at=run.created_at,
        updated_at=run.updated_at,
        last_progress_at=run.last_progress_at,
        failed_stage=run.failed_stage,
        error=None,
    )
