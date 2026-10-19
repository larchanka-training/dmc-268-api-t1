"""Общий контракт HTTP-слоя: базы моделей, тип времени, этап сбоя и модели, которые отдают
несколько ручек.

Модель, нужная одной ручке, живёт в модуле этой ручки; сюда — когда у неё появляется
второй потребитель, чтобы ручки не импортировали друг друга.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    WithJsonSchema,
)
from pydantic.alias_generators import to_camel

from app.domain.enums import MergeRequestState, Provider, ReviewRunStatus, TriggerSource

# Форма времени в каждом ответе: UTC, секунды, при наличии — микросекунды, суффикс `Z`.
UTC_DATETIME_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?Z$"


def _to_utc(moment: datetime) -> datetime:
    return moment.astimezone(UTC)


# Время в моделях API — только этот тип: принимает время с зоной, хранит в UTC, в JSON
# отдаёт `2026-10-11T10:00:00Z`. В схеме — `format: date-time` с `pattern` этой формы; по
# `pattern` тест находит поле времени, объявленное мимо `UtcDatetime`.
UtcDatetime = Annotated[
    AwareDatetime,
    AfterValidator(_to_utc),
    WithJsonSchema(
        {
            "type": "string",
            "format": "date-time",
            "pattern": UTC_DATETIME_PATTERN,
            "examples": ["2026-10-11T10:00:00Z"],
        }
    ),
]

# Этап, на котором прогон может упасть, — те же значения, что `FAILURE_STAGES` домена
# (равенство множеств проверяет тест).
FailedStage = Literal[ReviewRunStatus.BUILDING_CONTEXT, ReviewRunStatus.ANALYSING, ReviewRunStatus.PUBLISHING]
FAILED_STAGE_DESCRIPTION = "Этап, на котором прогон упал; null, если прогон не падал."


class CamelCaseResponse(BaseModel):
    """База моделей ответа: снаружи camelCase, в коде snake_case.

    В схеме обязательны все поля, в том числе со значением по умолчанию: сервер отдаёт их
    всегда, nullable-поле приходит как `null`, а не пропадает.
    """

    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=True, json_schema_serialization_defaults_required=True
    )


class CamelCaseRequest(BaseModel):
    """База моделей тела запроса: только camelCase, без лишних полей (`additionalProperties:
    false` в схеме) и без приведения типов — число строкой и строка числом не принимаются."""

    model_config = ConfigDict(
        alias_generator=to_camel, validate_by_name=False, validate_by_alias=True, extra="forbid", strict=True
    )


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


class ReviewError(CamelCaseResponse):
    """Структурированная ошибка прогона (§8 PIPELINE_SPEC.md) — не ошибка HTTP-запроса.

    Коды SCM_* провайдер-нейтральны: второй провайдер не ломает контракт.
    """

    code: ReviewErrorCode = Field(description="Код ошибки прогона.", examples=["LLM_TIMEOUT"])
    message: str = Field(description="Что случилось — по-русски, для показа пользователю.")
    retryable: bool = Field(description="Повторный запуск может пройти: сбой временный.")
    scm_provider: Provider | None = Field(
        description=(
            "Заполнено для SCM_AUTH_ERROR/SCM_UNAVAILABLE, иначе null. Называется scmProvider, "
            "а не provider: поле про сторону SCM, providerAttempt считает попытки и LLM, и SCM."
        )
    )
    provider_attempt: int | None = Field(
        ge=1,
        description="Попытка вызова LLM/SCM провайдера, с 1. Не путать с redelivery сообщения в очереди.",
    )


class ReviewJob(CamelCaseResponse):
    """Прогон ревью, поставленный в очередь, — публичная проекция записи review_runs.

    Не путать с сообщением очереди: у него нет собственной идентичности, отдельной от id прогона.
    """

    id: UUID = Field(description="Идентификатор прогона (reviewRunId).")
    provider: Provider = Field(description="Хостинг репозитория.", examples=["github"])
    provider_repository_id: str = Field(
        description="Идентификатор репозитория у провайдера (repositories.provider_id).", examples=["123456789"]
    )
    pull_request_number: int = Field(ge=1, description="Номер запроса на изменение у провайдера.", examples=[42])
    trigger: TriggerSource = Field(description="Что запустило прогон.", examples=["webhook"])
    head_commit_sha: str | None = Field(
        default=None,
        description="Коммит, который ревьюится.",
        examples=["a1b2c3d4e5f6789012345678abcdef0123456789"],
    )
    base_commit_sha: str | None = Field(
        default=None,
        description="Коммит, относительно которого считается дифф; null, пока воркер его не узнал.",
        examples=["fedcba0987654321fedcba0987654321fedcba09"],
    )
    status: ReviewRunStatus = Field(description="Текущий статус прогона.", examples=["queued"])
    findings_count: int = Field(ge=0, description="Сколько находок записано; у нового прогона 0.")
    rejected_findings: int = Field(
        ge=0, description="Сколько находок отброшено: привязка вне изменённых строк диффа."
    )
    model: str | None = Field(default=None, description="Модель LLM, которая провела ревью; null до анализа.")
    tokens_used: int | None = Field(default=None, ge=0, description="Токенов LLM потрачено; null до анализа.")
    duration_seconds: float | None = Field(
        default=None, ge=0, description="Длительность прогона в секундах; null, пока прогон не завершён."
    )
    created_at: UtcDatetime = Field(description="Когда прогон создан.")
    updated_at: UtcDatetime = Field(description="Когда запись последний раз менялась.")
    last_progress_at: UtcDatetime = Field(
        description="Обновляется на каждом переходе состояния; по нему находят зависшие прогоны (§7.5 PIPELINE_SPEC.md)."
    )
    failed_stage: FailedStage | None = Field(default=None, description=FAILED_STAGE_DESCRIPTION)
    error: ReviewError | None = Field(description="Ошибка прогона; null, если прогон не упал.")


class MergeRequestBase(BaseModel):
    """Поля запроса на изменение, общие для списка запросов и сводки в прогоне ревью."""

    id: UUID
    number: PositiveInt = Field(description="Номер запроса на изменение у провайдера.", examples=[42])
    title: str = Field(description="Заголовок у провайдера.")
    author: str = Field(description="Логин автора у провайдера.", examples=["octocat"])
    source_branch: str = Field(description="Ветка с изменениями.", examples=["feat/login"])
    target_branch: str = Field(description="Ветка, в которую предлагается слить.", examples=["develop"])
    state: MergeRequestState = Field(description="Состояние у провайдера.", examples=["open"])
