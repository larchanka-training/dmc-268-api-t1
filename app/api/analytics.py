"""Аналитика кабинета: замечания по дням и длительность прогонов за период (задача #282).

Правила выборки — `app/domain/analytics.py`, агрегаты считает база (`ReviewRunRepo.analytics`).
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from http import HTTPStatus
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, model_validator
from pydantic_core import InitErrorDetails, PydanticCustomError, ValidationError

from app.api.dependencies import (
    get_token_provider,
    get_unit_of_work_factory,
    get_visibility_catalog,
)
from app.api.errors import (
    ERRORS_404_REPOSITORY,
    ERRORS_422,
    ERRORS_502_SCM_VISIBILITY,
    ApiError,
)
from app.api.problems import ErrorCode
from app.api.repositories import user_repositories
from app.api.reviews import VISIBILITY_RULE
from app.api.schemas import UtcDatetime, UtcDatetimeParam
from app.api.session import SESSION_REQUIRED, SESSION_RESPONSES, current_session
from app.application.ports import InstallationCatalog, OAuthProvider, UnitOfWork
from app.application.run_feed_events import visible_repository_ids
from app.application.use_cases.auth import SignedIn
from app.domain.analytics import (
    DEFAULT_PERIOD,
    MAX_PERIOD,
    AnalyticsFilter,
)
from app.domain.enums import FindingCategory, FindingSeverity

_DURATION = "Длительность прогона в секундах — от захвата воркером до завершения."
_DESCRIPTION = (
    "Требует сессию; параметры проверяются раньше обращения к GitHub. Агрегаты дашборда за период: "
    "замечания по UTC-дням, категориям и важности; число, средняя и p95 длительность прогонов — всего "
    "и по каждому репозиторию. " + VISIBILITY_RULE + " Учитываются прогоны в статусе completed, "
    "сделавшие ревью: прогон без денег (coverage_reason = balance) модель не вызывал и в аналитику "
    "не входит. Считаются новые замечания: повтор открытого или закрытого человеком треда не "
    "считается. Период — по created_at прогона: created_from включительно, created_to исключительно, "
    f"время с зоной; по умолчанию created_to — сейчас, created_from — за {DEFAULT_PERIOD.days} дней до "
    "created_to. Замечание относится к UTC-дню своего прогона. Крайние дни периода неполные, дней без "
    "замечаний в findings_by_day нет: нули по периоду достраивает фронтенд. created_from позже "
    "created_to — 422 VALIDATION_ERROR в query.created_to, а без created_to — в query.created_from; "
    f"период длиннее {MAX_PERIOD.days} дней — 422 VALIDATION_ERROR в query.created_from. repository_id — только этот репозиторий; невидимый "
    "или несуществующий — 404 REPOSITORY_NOT_FOUND, как у GET /repositories/{repository_id}. В "
    "by_repository — только репозитории, у которых в периоде есть учтённые прогоны, по full_name."
)

router = APIRouter(
    prefix="/api/v1/analytics",
    tags=["analytics"],
    dependencies=SESSION_REQUIRED,
    responses=SESSION_RESPONSES,
)


class AnalyticsQuery(BaseModel):
    """Параметры запроса аналитики; после проверки обе границы периода заполнены."""

    repository_id: UUID | None = Field(default=None, description="Только этот репозиторий; не задан — все видимые.")
    created_from: UtcDatetimeParam | None = Field(
        default=None,
        description=(
            "Начало периода (включительно), время с зоной; "
            f"по умолчанию — за {DEFAULT_PERIOD.days} дней до created_to."
        ),
    )
    created_to: UtcDatetimeParam | None = Field(
        default=None, description="Конец периода (исключительно), время с зоной; по умолчанию — сейчас."
    )

    @model_validator(mode="after")
    def _period(self) -> Self:
        end = self.created_to or datetime.now(UTC)
        start = self.created_from or end - DEFAULT_PERIOD
        if start > end:
            # Без created_to конец — сейчас: виновато начало в будущем.
            bound, code = ("created_to" if self.created_to else "created_from"), "range_reversed"
        elif end - start > MAX_PERIOD:
            bound, code = "created_from", "period_too_long"
        else:
            self.created_from, self.created_to = start, end
            return self
        # ValidationError, а не ValueError: только так ошибка модели получает поле в `loc`.
        raise ValidationError.from_exception_data(
            type(self).__name__,
            [InitErrorDetails(type=PydanticCustomError(code, code), loc=(bound,), input=getattr(self, bound))],
        )


class FindingsDayCountResponse(BaseModel):
    """Сколько замечаний одной категории и важности нашли прогоны одного UTC-дня."""

    date: dt.date = Field(description="UTC-день created_at прогона.", examples=["2026-11-10"])
    category: FindingCategory
    severity: FindingSeverity
    count: int = Field(ge=1)


class RunDurationsResponse(BaseModel):
    """Учтённые прогоны периода и их длительность."""

    count: int = Field(ge=0, description="Сколько прогонов учтено.")
    average_duration_seconds: float | None = Field(description=f"Средняя. {_DURATION} null — прогонов нет.")
    p95_duration_seconds: float | None = Field(
        description=f"95-й перцентиль с интерполяцией (percentile_cont). {_DURATION} null — прогонов нет."
    )


class RepositoryAnalyticsResponse(BaseModel):
    """Аналитика одного репозитория за период."""

    repository_id: UUID
    full_name: str = Field(description="Имя репозитория, записанное при подключении.", examples=["octo-org/api"])
    runs_count: int = Field(ge=1, description="Сколько прогонов учтено.")
    average_duration_seconds: float = Field(description=f"Средняя. {_DURATION}")
    p95_duration_seconds: float = Field(description=f"95-й перцентиль (percentile_cont). {_DURATION}")
    findings_count: int = Field(ge=0, description="Замечания учтённых прогонов.")


class AnalyticsResponse(BaseModel):
    """Агрегаты дашборда кабинета за период."""

    created_from: UtcDatetime = Field(description="Начало периода, включительно.")
    created_to: UtcDatetime = Field(description="Конец периода, исключительно.")
    findings_by_day: list[FindingsDayCountResponse] = Field(
        description="По дню, затем по категории и важности в порядке перечислений; пар без замечаний нет."
    )
    runs: RunDurationsResponse
    by_repository: list[RepositoryAnalyticsResponse] = Field(description="По full_name; только репозитории с прогонами.")


@router.get(
    "",
    response_model=AnalyticsResponse,
    summary="Аналитика замечаний и длительности прогонов",
    response_description="Агрегаты за период.",
    description=_DESCRIPTION,
    responses={**ERRORS_404_REPOSITORY, **ERRORS_422, **ERRORS_502_SCM_VISIBILITY},
)
def get_analytics(
    query: Annotated[AnalyticsQuery, Query()],
    signed_in: SignedIn = Depends(current_session),
    catalog: InstallationCatalog = Depends(get_visibility_catalog),
    oauth: OAuthProvider = Depends(get_token_provider),
    uow_factory: Callable[[], UnitOfWork] = Depends(get_unit_of_work_factory),
) -> AnalyticsResponse:
    # Видимые — вызовом, а не зависимостью: 422 не должен стоить запроса в GitHub.
    visible = visible_repository_ids(uow_factory(), user_repositories(signed_in, catalog, oauth, uow_factory))
    if query.repository_id is not None:
        if query.repository_id not in visible:
            raise ApiError(HTTPStatus.NOT_FOUND, ErrorCode.REPOSITORY_NOT_FOUND, "Репозитория с таким id нет.")
        visible = frozenset({query.repository_id})
    start, end = query.created_from, query.created_to
    assert start is not None and end is not None  # заполнил AnalyticsQuery._period
    with uow_factory() as uow:
        result = uow.review_runs.analytics(AnalyticsFilter(visible, start, end))
    return AnalyticsResponse.model_validate({"created_from": start, "created_to": end, **asdict(result)})
