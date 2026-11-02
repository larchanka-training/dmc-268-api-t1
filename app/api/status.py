"""Статус сервиса для публичной страницы: `GET /api/v1/status`.

Открыт без сессии: страницу статуса читают и гости. Статус — из сработавших критических
алертов Prometheus (`app/domain/service_status.py`); наружу уходят только статусы
компонентов — ни имён алертов, ни меток, ни адресов, ни причин сбоя источника.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.dependencies import get_service_status_monitor
from app.api.schemas import UtcDatetime
from app.application.use_cases.service_status import ServiceStatusMonitor
from app.domain.enums import ServiceComponent, ServiceStatus

router = APIRouter(prefix="/api/v1", tags=["status"])

_STATUS_DESCRIPTION = (
    "operational — работает; degraded — работает со сбоями; outage — недоступен; "
    "unknown — проверить не удалось: мониторинг не настроен или не отвечает, либо не видит "
    "сигналов, на которых держится проверка."
)


class ComponentStatusResponse(BaseModel):
    """Состояние одного компонента сервиса."""

    id: ServiceComponent = Field(
        description="Компонент: api — HTTP API, worker — воркер ревью, queue — очередь задач (брокер).",
        examples=["api"],
    )
    status: ServiceStatus = Field(description=f"Состояние компонента. {_STATUS_DESCRIPTION}", examples=["operational"])


class ServiceStatusResponse(BaseModel):
    """Состояние сервиса для страницы статуса."""

    status: ServiceStatus = Field(
        description=(
            "Сводное состояние — худшее из компонентов в порядке outage, degraded, unknown, "
            f"operational. {_STATUS_DESCRIPTION}"
        ),
        examples=["operational"],
    )
    components: list[ComponentStatusResponse] = Field(
        description="Все компоненты, всегда в порядке api, worker, queue.",
    )
    checked_at: UtcDatetime = Field(
        description="Когда сервер оценил состояние; ответ может быть из кэша не старше 15 секунд.",
    )


@router.get(
    "/status",
    response_model=ServiceStatusResponse,
    openapi_extra={"security": []},
    summary="Статус сервиса",
    description=(
        "Открыт без сессии. Состояние API, воркера и очереди по сработавшим "
        "критическим алертам мониторинга; оценка кэшируется на сервере на 15 секунд. "
        "Недоступный мониторинг — не ошибка: ответ 200, компоненты unknown."
    ),
    response_description="Сводное состояние и состояние каждого компонента.",
)
def get_service_status(monitor: ServiceStatusMonitor = Depends(get_service_status_monitor)) -> ServiceStatusResponse:
    report = monitor.current()
    return ServiceStatusResponse(
        status=report.status,
        components=[ComponentStatusResponse(id=c.component, status=c.status) for c in report.components],
        checked_at=report.checked_at,
    )
