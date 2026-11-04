"""Статус сервиса для публичной страницы (change `add-status-page`).

Ручка открыта без сессии, поэтому оценка кэшируется в памяти процесса на `ttl_seconds`:
поток запросов к странице не превращается в поток запросов к Prometheus. Чаще и не нужно —
Prometheus сам пересчитывает правила раз в `evaluation_interval` (минута по умолчанию).

Устаревшую оценку обновляет один поток — тот, что взял замок. Остальные его не ждут и
отдают прошлую оценку: ручка синхронная, и ожидающие занимали бы потоки общего threadpool
на весь поход в источник (до его тайм-аута). Ждут только запросы до первой оценки — отдать
им нечего. В кэше — оценка целиком, вместе с `checked_at`: время в ответе — когда сервис
действительно проверял, а не когда отдал запись из кэша. Неудача источника кэшируется так же
— `unknown` на тот же срок.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from app.application.ports.alert_source import AlertSource
from app.domain.service_status import ServiceStatusReport, evaluate_service_status


@dataclass(frozen=True, slots=True)
class _Cached:
    """Оценка и момент `monotonic`, после которого она устарела — одной ссылкой: поток без
    замка читает их вместе, не видя оценку от одного обновления, а срок от другого."""

    report: ServiceStatusReport
    expires_at: float


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
        self._cached: _Cached | None = None

    def current(self) -> ServiceStatusReport:
        """Оценка не старше `ttl_seconds`; пока её обновляет другой поток — прошлая."""
        cached = self._cached
        if cached is not None and self._monotonic() < cached.expires_at:
            return cached.report
        if cached is None:
            self._lock.acquire()
        elif not self._lock.acquire(blocking=False):
            return cached.report
        try:
            # Пока ждали замок или читали без него, оценку мог обновить другой поток.
            cached = self._cached
            if cached is None or self._monotonic() >= cached.expires_at:
                report = evaluate_service_status(self._source.firing_alerts(), self._now())
                cached = _Cached(report, self._monotonic() + self._ttl_seconds)
                self._cached = cached
            return cached.report
        finally:
            self._lock.release()
