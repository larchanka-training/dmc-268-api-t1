"""Модели ответов, которые отдают несколько ручек — общий контракт HTTP-слоя.

Модель, нужная одной ручке, живёт в модуле этой ручки; сюда — когда у неё появляется
второй потребитель, чтобы ручки не импортировали друг друга.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.domain.enums import Provider, ReviewRunStatus, TriggerSource


class CamelCaseModel(BaseModel):
    """Поля в camelCase снаружи, в snake_case в коде."""


    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)


ReviewErrorCode = Literal[
    "SCM_AUTH_ERROR",
    "PR_NOT_FOUND",
    "REPOSITORY_NOT_FOUND",
    "SCM_UNAVAILABLE",
    "CONTEXT_PARSING_ERROR",
    "LLM_RATE_LIMITED",
    "LLM_TIMEOUT",
    "LLM_UNAVAILABLE",
    "LLM_INVALID_OUTPUT",
    "REVIEW_RUN_STALE",
    "INTERNAL_ERROR",
]


class ReviewError(CamelCaseModel):
    """Структурированная ошибка прогона (§8 PIPELINE_SPEC.md) — не ошибка HTTP-запроса.

    Коды SCM_* провайдер-нейтральны: второй провайдер не ломает контракт.
    """

    code: ReviewErrorCode
    message: str
    retryable: bool
    scm_provider: Provider | None = Field(
        description=(
            "Заполнено для SCM_AUTH_ERROR/SCM_UNAVAILABLE, иначе null. Называется scmProvider, "
            "а не provider: поле про сторону SCM, providerAttempt считает попытки и LLM, и SCM."
        )
    )
    provider_attempt: int | None = Field(
        ge=1,
        description="Попытка вызова LLM/SCM провайдера. Не путать с redelivery сообщения в очереди.",
    )


class ReviewJob(CamelCaseModel):
    """Публичная проекция прогона ревью (review_runs) в camelCase.

    Не путать с сообщением очереди: у него нет собственной идентичности, отдельной от id прогона.
    """

    id: str = Field(description="Идентификатор прогона (reviewRunId).")
    provider: Provider
    provider_repository_id: str
    pull_request_number: int = Field(ge=1)
    trigger: TriggerSource
    head_commit_sha: str | None = None
    base_commit_sha: str | None = None
    status: ReviewRunStatus
    findings_count: int = Field(ge=0)
    rejected_findings: int = Field(ge=0)
    model: str | None = None
    tokens_used: int | None = Field(default=None, ge=0)
    duration_seconds: float | None = Field(default=None, ge=0)
    created_at: datetime
    updated_at: datetime
    last_progress_at: datetime = Field(
        description="Обновляется на каждом переходе состояния; по нему находят зависшие прогоны (§7.5 PIPELINE_SPEC.md)."
    )
    failed_stage: ReviewRunStatus | None = Field(
        default=None,
        description="Этап, на котором прогон упал (building_context, analysing, publishing); null, если не падал.",
    )
    error: ReviewError | None
