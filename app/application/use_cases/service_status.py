"""Статус сервиса для публичной страницы (change `add-status-page`).

Ручка открыта без сессии, поэтому оценка кэшируется в памяти процесса на `ttl_seconds`:
поток запросов к странице не превращается в поток запросов к Prometheus. Чаще и не нужно —
Prometheus сам пересчитывает правила раз в `evaluation_interval` (минута по умолчанию).

Обновление идёт под замком: запросы, пришедшие, пока кэш устарел, ждут один поход в
источник (не дольше его тайм-аута), а не идут в него каждый сам. В кэше — оценка целиком,
вместе с `checked_at`: время в ответе — когда сервис действительно проверял, а не когда
отдал запись из кэша. Неудача источника кэшируется так же — `unknown` на тот же срок.
"""

import threading
from collections.abc import Callable
from datetime import datetime

from app.application.ports.alert_source import AlertSource
from app.domain.service_status import ServiceStatusReport, evaluate_service_status


class ServiceStatusMonitor:
    def __init__(
        self,
        source: AlertSource,
        *,
        ttl_seconds: float,
        monotonic: Callable[[], float],
        now: Callable[[], datetime],
    ) -> None:
        self._source = source
        self._ttl_seconds = ttl_seconds
        self._monotonic = monotonic
        self._now = now
        self._lock = threading.Lock()
        self._report: ServiceStatusReport | None = None
        self._expires_at = 0.0

    def current(self) -> ServiceStatusReport:
        """Оценка не старше `ttl_seconds`; устарела — новая по алертам источника."""
        with self._lock:
            if self._report is None or self._monotonic() >= self._expires_at:
                self._report = evaluate_service_status(self._source.firing_alerts(), self._now())
                self._expires_at = self._monotonic() + self._ttl_seconds
            return self._report
