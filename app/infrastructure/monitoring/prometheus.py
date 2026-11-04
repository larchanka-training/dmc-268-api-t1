"""Адаптер `AlertSource`: сработавшие алерты из HTTP API Prometheus.

Один instant-запрос `ALERTS{alertstate="firing"}` к `/api/v1/query` — тот же ряд, по
которому Prometheus отдаёт алерты Alertmanager. Адаптер только переводит ответ в
`FiringAlert`; что алерт значит для страницы статуса — домен
(`app/domain/service_status.py`).

Любой сбой — `None`, а не исключение: недоступный Prometheus, тайм-аут, ответ не `2xx`,
тело не по формату. Страница статуса тогда честно говорит `unknown`. В лог — одно
предупреждение с причиной: тип исключения или HTTP-статус, без тела ответа и меток алертов.
Без адреса (`PROMETHEUS_URL` пуст) запроса нет вовсе. Порт не импортируется — совпадение
структурное.
"""

import logging
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.service_status import FiringAlert

_log = logging.getLogger(__name__)

QUERY_PATH = "/api/v1/query"
FIRING_ALERTS_QUERY = 'ALERTS{alertstate="firing"}'


class _PrometheusModel(BaseModel):
    """Тело ответа Prometheus: строгие типы, лишние поля игнорируются."""

    model_config = ConfigDict(strict=True, extra="ignore", frozen=True)


class _Sample(_PrometheusModel):
    # Метки ряда `ALERTS`: `__name__`, `alertname`, `alertstate` и метки алерта.
    metric: dict[str, str]


class _Vector(_PrometheusModel):
    result_type: Literal["vector"] = Field(alias="resultType")
    result: list[_Sample]


class _QueryResponse(_PrometheusModel):
    status: Literal["success"]
    data: _Vector


class PrometheusAlertSource:
    """`client` — с `base_url` Prometheus и тайм-аутом; `None` — Prometheus не задан."""

    def __init__(self, client: httpx.Client | None) -> None:
        self._client = client

    def firing_alerts(self) -> tuple[FiringAlert, ...] | None:
        if self._client is None:
            return None
        try:
            response = self._client.get(QUERY_PATH, params={"query": FIRING_ALERTS_QUERY})
        except httpx.HTTPError as error:
            _log.warning("алерты Prometheus не получены (%s): статус сервиса — unknown", type(error).__name__)
            return None
        if not response.is_success:
            _log.warning(
                "алерты Prometheus не получены (HTTP %d): статус сервиса — unknown", response.status_code
            )
            return None
        try:
            body = _QueryResponse.model_validate_json(response.content)
        except ValidationError:
            _log.warning("ответ Prometheus не по формату: статус сервиса — unknown")
            return None
        return tuple(
            FiringAlert(sample.metric["alertname"], sample.metric)
            for sample in body.data.result
            if "alertname" in sample.metric
        )
