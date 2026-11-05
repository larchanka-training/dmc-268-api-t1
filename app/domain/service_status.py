"""Статус сервиса для публичной страницы — из сработавших критических алертов (#100).

Источник правды — те же правила, что будят команду (`infra/observability/alerts.yml`):
страница не заводит своих проверок и не расходится с алертами. Решение — чистая функция над
списком сработавших алертов; сходить за ним в Prometheus — дело адаптера, время проверки
приходит аргументом.

Каждый компонент получает тяжелейший из статусов, которые дают сработавшие по нему алерты
(`ALERT_RULES`); ни одного — `operational`. Кроме алертов о самом компоненте, в таблице —
алерты базы (без неё недоступны api и worker) и алерты слепоты: Prometheus не снимает
источник, на котором держатся алерты компонента, или метрик контейнеров нет вовсе. Тогда
молчание алертов ничего не значит, и компонент — `unknown`, а не `operational`.

Порядок тяжести один на компонент и на сервис целиком: `outage` > `degraded` > `unknown` >
`operational`. Сработавший алерт — подтверждённая проблема, а слепота — только отсутствие
подтверждения, поэтому `degraded` при частично слепых источниках остаётся `degraded`: прятать
известный сбой за «неизвестно» — хуже для читателя страницы. Алертов нет совсем (Prometheus
не задан, недоступен, ответил не по формату) — все компоненты `unknown`.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType

from app.domain.enums import ServiceComponent, ServiceStatus

# Тяжесть статуса: больше — хуже. Сводный статус и статус компонента — максимум по ней.
SERVICE_STATUS_SEVERITY: Mapping[ServiceStatus, int] = MappingProxyType(
    {
        ServiceStatus.OPERATIONAL: 0,
        ServiceStatus.UNKNOWN: 1,
        ServiceStatus.DEGRADED: 2,
        ServiceStatus.OUTAGE: 3,
    }
)

_ALL = tuple(ServiceComponent)
_API = ServiceComponent.API
_WORKER = ServiceComponent.WORKER
_QUEUE = ServiceComponent.QUEUE


@dataclass(frozen=True, slots=True)
class FiringAlert:
    """Сработавший алерт: имя правила и метки ряда `ALERTS`."""

    name: str
    labels: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AlertRule:
    """Строка таблицы: алерт с такими метками даёт этим компонентам этот статус."""

    alert: str
    status: ServiceStatus
    components: tuple[ServiceComponent, ...]
    labels: Mapping[str, str] = field(default_factory=dict)

    def matches(self, alert: FiringAlert) -> bool:
        return alert.name == self.alert and all(alert.labels.get(k) == v for k, v in self.labels.items())


def _container(name: str) -> Mapping[str, str]:
    return {"container_name": name}


# Имена алертов и меток — из `infra/observability/alerts.yml`, имена job — из
# `prometheus.yml`; совпадение сверяет `tests/domain/test_service_status.py`.
ALERT_RULES: tuple[AlertRule, ...] = (
    # API: контейнер не запущен или /health не отвечает — недоступен; 5xx и перезапуски — сбои.
    AlertRule("ContainerDown", ServiceStatus.OUTAGE, (_API,), _container("dmc268-api")),
    AlertRule("ServiceProbeFailed", ServiceStatus.OUTAGE, (_API,), {"service": "api"}),
    AlertRule("Api5xxSpike", ServiceStatus.DEGRADED, (_API,)),
    AlertRule("ContainerRestarting", ServiceStatus.DEGRADED, (_API,), _container("dmc268-api")),
    # Воркер: не запущен или не забирает задачи — ревью не идут; падающие прогоны и
    # перезапуски — идут с ошибками. ReviewDlqNotEmpty здесь нет: у DLQ нет TTL, и одно
    # ядовитое сообщение держало бы degraded, пока DLQ не разберут руками, а падающие
    # прогоны и так ловит ReviewRunsFailing.
    AlertRule("ContainerDown", ServiceStatus.OUTAGE, (_WORKER,), _container("dmc268-worker")),
    AlertRule("WorkerNotConsuming", ServiceStatus.OUTAGE, (_WORKER,)),
    # Подписан, но задачи лежат без подтверждений. По метрикам это не отличить от медленной модели
    # с фоллбэком, и очередь всё равно разойдётся, моделью или выметателем: degraded, а не outage.
    AlertRule("WorkerStalled", ServiceStatus.DEGRADED, (_WORKER,)),
    AlertRule("ContainerRestarting", ServiceStatus.DEGRADED, (_WORKER,), _container("dmc268-worker")),
    AlertRule("ReviewRunsFailing", ServiceStatus.DEGRADED, (_WORKER,)),
    # Висит одна задача: ждёт один прогон или, с очередью за ним, все (тогда горит и WorkerStalled).
    AlertRule("ReviewJobStuck", ServiceStatus.DEGRADED, (_WORKER,)),
    # Очередь: брокер не запущен или не принимает AMQP — недоступна; перезапуски — сбои.
    AlertRule("ContainerDown", ServiceStatus.OUTAGE, (_QUEUE,), _container("dmc268-rabbitmq")),
    AlertRule("ServiceProbeFailed", ServiceStatus.OUTAGE, (_QUEUE,), {"service": "rabbitmq"}),
    AlertRule("ContainerRestarting", ServiceStatus.DEGRADED, (_QUEUE,), _container("dmc268-rabbitmq")),
    # База: отдельного компонента нет, но без неё API отвечает 500 на всё, кроме /health
    # (он базу не проверяет), а воркер не берёт и не завершает прогоны — оба недоступны.
    AlertRule("ContainerDown", ServiceStatus.OUTAGE, (_API, _WORKER), _container("dmc268-postgres")),
    AlertRule("ServiceProbeFailed", ServiceStatus.OUTAGE, (_API, _WORKER), {"service": "postgres"}),
    # Слепота. docker-stats — ContainerDown и ContainerRestarting всех трёх и базы; без
    # рядов контейнеров при живом скрейпе (ContainerMetricsMissing) — то же самое.
    # ContainerMemoryNearLimit (warning) страницу не трогает: статус — по критическим алертам.
    AlertRule("ScrapeTargetDown", ServiceStatus.UNKNOWN, _ALL, {"job": "docker-stats"}),
    AlertRule("ContainerMetricsMissing", ServiceStatus.UNKNOWN, _ALL),
    # blackbox-services — пробы /health API, порта базы и AMQP-порта брокера
    # (ServiceProbeFailed); проба базы держит api и worker.
    AlertRule("ScrapeTargetDown", ServiceStatus.UNKNOWN, _ALL, {"job": "blackbox-services"}),
    # collector — метрики приложения по OTLP: 5xx API (Api5xxSpike) и исходы прогонов
    # воркера (ReviewRunsFailing). Ряды приложения несут job = service.name
    # (dmc-268-api, honor_labels), но `up` — всегда job скрейпа.
    AlertRule("ScrapeTargetDown", ServiceStatus.UNKNOWN, (_API, _WORKER), {"job": "collector"}),
    # rabbitmq — метрики очередей: потребители (WorkerNotConsuming), готовые, выданные и
    # подтверждённые задачи (WorkerStalled, ReviewJobStuck).
    AlertRule("ScrapeTargetDown", ServiceStatus.UNKNOWN, (_WORKER,), {"job": "rabbitmq"}),
)


@dataclass(frozen=True, slots=True)
class ComponentStatus:
    component: ServiceComponent
    status: ServiceStatus


@dataclass(frozen=True, slots=True)
class ServiceStatusReport:
    """Сводный статус, компоненты в порядке `ServiceComponent` и время проверки."""

    status: ServiceStatus
    components: tuple[ComponentStatus, ...]
    checked_at: datetime


def worst_status(statuses: Sequence[ServiceStatus]) -> ServiceStatus:
    """Тяжелейший статус; пустой список — `operational`."""
    return max(statuses, key=SERVICE_STATUS_SEVERITY.__getitem__, default=ServiceStatus.OPERATIONAL)


def evaluate_service_status(alerts: Sequence[FiringAlert] | None, checked_at: datetime) -> ServiceStatusReport:
    """Статус сервиса по сработавшим алертам; `None` — алерты неизвестны, всё `unknown`.

    Алерты, которых нет в `ALERT_RULES` (диск, реплика и копии базы, бюджет модели, DLQ,
    другие контейнеры), на статус не влияют: странице нужны только компоненты, которые видит
    пользователь, и то, без чего они не работают.
    """
    components = tuple(
        ComponentStatus(
            component,
            ServiceStatus.UNKNOWN
            if alerts is None
            else worst_status(
                [
                    rule.status
                    for rule in ALERT_RULES
                    if component in rule.components
                    for alert in alerts
                    if rule.matches(alert)
                ]
            ),
        )
        for component in ServiceComponent
    )
    return ServiceStatusReport(worst_status([c.status for c in components]), components, checked_at)
