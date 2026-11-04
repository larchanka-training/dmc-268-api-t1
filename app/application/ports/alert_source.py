"""Источник сработавших алертов для страницы статуса сервиса.

Адаптер — HTTP API Prometheus (`app/infrastructure/monitoring/prometheus.py`): он же
вычисляет правила `infra/observability/alerts.yml`. Порт — ради границы слоя (слой
приложения не знает httpx) и подмены в тестах.
"""

from typing import Protocol

from app.domain.service_status import FiringAlert


class AlertSource(Protocol):
    def firing_alerts(self) -> tuple[FiringAlert, ...] | None:
        """Сработавшие алерты; `None` — узнать не удалось: источник не задан, недоступен,
        ответил ошибкой или не по формату.

        Не бросает: сбой источника — штатный исход для страницы статуса (`unknown`), а не
        ошибка запроса.
        """
        ...
