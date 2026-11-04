"""Кэш оценки статуса сервиса: один поход в источник за TTL, `checked_at` — время оценки,
обновление не держит остальные запросы."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from app.application.use_cases.service_status import ServiceStatusMonitor
from app.domain.enums import ServiceStatus
from app.domain.service_status import FiringAlert

START = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


class _Source:
    def __init__(self, *answers: tuple[FiringAlert, ...] | None) -> None:
        self._answers = list(answers)
        self.calls = 0

    def firing_alerts(self) -> tuple[FiringAlert, ...] | None:
        self.calls += 1
        return self._answers.pop(0)


class _SlowSource(_Source):
    """Отвечает, только когда тест откроет `gate`: обновление «висит» в источнике."""

    def __init__(self, *answers: tuple[FiringAlert, ...] | None) -> None:
        super().__init__(*answers)
        self.entered = threading.Event()
        self.gate = threading.Event()

    def firing_alerts(self) -> tuple[FiringAlert, ...] | None:
        self.entered.set()
        assert self.gate.wait(timeout=5)
        return super().firing_alerts()


class _Clock:
    def __init__(self) -> None:
        self.seconds = 100.0

    def monotonic(self) -> float:
        return self.seconds

    def now(self) -> datetime:
        return START + timedelta(seconds=self.seconds - 100.0)


def _monitor(source: _Source, clock: _Clock) -> ServiceStatusMonitor:
    return ServiceStatusMonitor(source, ttl_seconds=15, monotonic=clock.monotonic, now=clock.now)


def test_the_report_is_cached_for_the_ttl() -> None:
    source = _Source((), (FiringAlert("WorkerNotConsuming"),))
    clock = _Clock()
    monitor = _monitor(source, clock)

    first = monitor.current()
    clock.seconds += 14.9
    assert monitor.current() is first
    assert source.calls == 1
    assert first.status is ServiceStatus.OPERATIONAL
    assert first.checked_at == START

    clock.seconds += 0.1
    second = monitor.current()
    assert source.calls == 2
    assert second.status is ServiceStatus.OUTAGE
    assert second.checked_at == START + timedelta(seconds=15)


def test_a_failed_source_is_cached_as_unknown_too() -> None:
    source = _Source(None, ())
    clock = _Clock()
    monitor = _monitor(source, clock)

    assert monitor.current().status is ServiceStatus.UNKNOWN
    assert monitor.current().status is ServiceStatus.UNKNOWN
    assert source.calls == 1

    clock.seconds += 15
    assert monitor.current().status is ServiceStatus.OPERATIONAL
    assert source.calls == 2


def test_while_one_request_refreshes_the_others_get_the_previous_report() -> None:
    source = _SlowSource((), (FiringAlert("WorkerNotConsuming"),))
    clock = _Clock()
    monitor = _monitor(source, clock)
    source.gate.set()
    previous = monitor.current()
    source.gate.clear()
    source.entered.clear()
    clock.seconds += 15

    with ThreadPoolExecutor(2) as pool:
        try:
            refreshing = pool.submit(monitor.current)
            assert source.entered.wait(timeout=5)
            # Обновление висит в источнике, а запрос рядом его не ждёт.
            assert pool.submit(monitor.current).result(timeout=1) is previous
        finally:
            source.gate.set()
        fresh = refreshing.result(timeout=5)

    assert fresh.status is ServiceStatus.OUTAGE
    assert monitor.current() is fresh
    assert source.calls == 2


def test_before_the_first_report_requests_wait_for_it() -> None:
    source = _SlowSource(())
    monitor = _monitor(source, _Clock())

    with ThreadPoolExecutor(2) as pool:
        try:
            first = pool.submit(monitor.current)
            assert source.entered.wait(timeout=5)
            second = pool.submit(monitor.current)
            # Отдать нечего: второй ждёт ту же оценку, а не идёт в источник сам.
            with pytest.raises(TimeoutError):
                second.result(timeout=0.1)
        finally:
            source.gate.set()
        assert second.result(timeout=5) is first.result(timeout=5)

    assert source.calls == 1
