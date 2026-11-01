"""Периодический проход воркера: зависшие прогоны, истёкшие сессии, зависшие и
истёкшие симуляции, без брокера.

Спан прохода проверяется на настоящей базе: нужны спаны запросов SQLAlchemy.
"""

import logging
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import create_engine

from app.application.ports import UnitOfWork
from app.config import Settings
from app.domain.entities import ReviewRun, Session, Simulation
from app.domain.enums import (
    ReviewRunStatus,
    SimulationSourceKind,
    SimulationStatus,
    TriggerSource,
)
from app.domain.simulation import SIMULATION_STALE, fail_simulation, new_simulation
from app.domain.webhook_redelivery import REDELIVERY_WINDOW, WebhookDelivery
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.telemetry import instrument_engine, sweep_span
from app.worker.factory import build_maintenance, build_redelivery

from ..conftest import requires_db
from ..fakes import FakeVcs, webhook_payload
from ..fakes.unit_of_work import FakeUnitOfWork
from .test_worker_loop import _strict, _worker

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _session(n: int, expires_at: datetime) -> Session:
    return Session(
        id=UUID(int=n),
        token_hash=bytes([n]),
        user_id=UUID(int=99),
        created_at=NOW - timedelta(days=30),
        expires_at=expires_at,
    )


def test_the_pass_purges_expired_sessions() -> None:
    uow = FakeUnitOfWork()
    uow.sessions.add(_session(1, NOW - timedelta(days=1)))
    uow.sessions.add(_session(2, NOW + timedelta(days=1)))

    _maintenance(lambda: uow)()

    assert [s.id for s in uow.sessions.stored] == [UUID(int=2)]


LIMIT = timedelta(minutes=30)
QUEUED_LIMIT = timedelta(hours=6)
SIMULATION_LIMIT = timedelta(minutes=30)


def _maintenance(uow_factory: Callable[[], UnitOfWork]) -> Callable[[], None]:
    return build_maintenance(
        uow_factory,
        stale_limit=LIMIT,
        queued_limit=QUEUED_LIMIT,
        simulation_stale_limit=SIMULATION_LIMIT,
        now=lambda: NOW,
    )


def _simulation(n: int, *, created: datetime, status: SimulationStatus = SimulationStatus.QUEUED) -> Simulation:
    simulation = new_simulation(
        simulation_id=UUID(int=n),
        user_id=UUID(int=n + 50),
        source_kind=SimulationSourceKind.DIFF,
        source="diff --git a/a b/a\n",
        rules="",
        file_path=None,
        now=created,
        ttl=timedelta(days=1),
    )
    return replace(simulation, status=status)


def test_the_pass_fails_stale_simulations_and_purges_expired_ones() -> None:
    uow = FakeUnitOfWork()
    uow.simulations.add(_simulation(1, created=NOW - SIMULATION_LIMIT - timedelta(seconds=1)))
    uow.simulations.add(_simulation(2, created=NOW - timedelta(minutes=1), status=SimulationStatus.ANALYSING))
    expired = fail_simulation(_simulation(3, created=NOW - timedelta(days=2)), "LLM_TIMEOUT", NOW).unwrap()
    uow.simulations.add(expired)

    _maintenance(lambda: uow)()

    stale = uow.simulations.get(UUID(int=1))
    assert stale is not None
    assert (stale.status, stale.error_code, stale.source) == (SimulationStatus.FAILED, SIMULATION_STALE, None)
    fresh = uow.simulations.get(UUID(int=2))
    assert fresh is not None and fresh.status is SimulationStatus.ANALYSING
    assert uow.simulations.get(UUID(int=3)) is None


def test_a_failing_simulation_step_does_not_stop_the_other(caplog, monkeypatch) -> None:
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(logging.getLogger("app.worker.factory"), "disabled", False)
    uow = FakeUnitOfWork()
    uow.simulations.add(fail_simulation(_simulation(3, created=NOW - timedelta(days=2)), "X", NOW).unwrap())
    calls = 0

    def uow_factory() -> FakeUnitOfWork:
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("база недоступна")
        return uow

    _maintenance(uow_factory)()

    assert uow.simulations.get(UUID(int=3)) is None
    (record,) = caplog.records
    assert record.maintenance_step == "sweep_stale_simulations"


def test_the_pass_sweeps_stale_runs() -> None:
    uow = FakeUnitOfWork()
    moment = NOW - timedelta(hours=1)
    uow.review_runs.add(
        ReviewRun(
            id=UUID(int=1),
            merge_request_id=UUID(int=99),
            head_sha="sha1",
            status=ReviewRunStatus.ANALYSING,
            trigger=TriggerSource.WEBHOOK,
            last_progress_at=moment,
            created_at=moment,
            updated_at=moment,
        )
    )

    _maintenance(lambda: uow)()

    run = uow.review_runs.get(UUID(int=1))
    assert run is not None and run.status is ReviewRunStatus.FAILED


def test_a_failing_step_is_logged_and_does_not_stop_the_other(caplog, monkeypatch) -> None:
    caplog.set_level(logging.ERROR)
    # `fileConfig` в тестах Alembic отключает уже созданные логгеры.
    # TODO: убрать после мёржа #65 (change fix-migration-logging), как и такой же
    # обход в tests/test_auth_api.py.
    monkeypatch.setattr(logging.getLogger("app.worker.factory"), "disabled", False)
    uow = FakeUnitOfWork()
    uow.sessions.add(_session(1, NOW - timedelta(days=1)))
    calls = 0

    def uow_factory() -> FakeUnitOfWork:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("база недоступна")
        return uow

    _maintenance(uow_factory)()

    # Упал первый шаг; сессии и оба шага симуляций прошли.
    assert calls == 4
    assert uow.sessions.stored == []
    (record,) = caplog.records
    assert record.getMessage() == "проход по зависшим прогонам завершился ошибкой"
    assert record.maintenance_step == "sweep_stale_runs"


def test_a_failing_step_marks_the_pass_span_as_an_error(
    spans: InMemorySpanExporter,
) -> None:
    uow = FakeUnitOfWork()
    uow.sessions.add(_session(1, NOW - timedelta(days=1)))
    calls = 0

    def uow_factory() -> FakeUnitOfWork:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("база недоступна")
        return uow

    with sweep_span():
        _maintenance(uow_factory)()

    # Второй шаг выполнен, хотя спан уже отмечен ошибкой первого.
    assert uow.sessions.stored == []
    (pass_span,) = [s for s in spans.get_finished_spans() if s.name == "review.sweep"]
    assert pass_span.status.status_code is StatusCode.ERROR
    assert pass_span.status.description == "RuntimeError"
    (event,) = pass_span.events
    assert event.name == "exception"
    assert event.attributes is not None
    assert event.attributes["exception.type"] == "RuntimeError"


def test_an_escaping_exception_marks_the_pass_span_with_its_type_only(
    spans: InMemorySpanExporter,
) -> None:
    with pytest.raises(RuntimeError), sweep_span():
        raise RuntimeError("DETAIL: Key (id)=(42) already exists")

    (pass_span,) = [s for s in spans.get_finished_spans() if s.name == "review.sweep"]
    assert pass_span.status.status_code is StatusCode.ERROR
    assert pass_span.status.description == "RuntimeError"
    (event,) = pass_span.events
    assert event.attributes is not None
    assert "already exists" in str(event.attributes["exception.message"])


@pytest.mark.integration
@requires_db
def test_the_queries_of_the_pass_are_children_of_its_span(
    clean_db, spans: InMemorySpanExporter
) -> None:
    # Свой движок: инструментирование вешает слушателей на движок навсегда, а
    # общий движок сессии тестов получил бы их повторно.
    engine = create_engine(clean_db.url)
    instrument_engine(engine)
    sweep = build_maintenance(
        lambda: SqlAlchemyUnitOfWork(engine),
        stale_limit=LIMIT,
        queued_limit=QUEUED_LIMIT,
        simulation_stale_limit=SIMULATION_LIMIT,
        now=lambda: NOW,
    )
    try:
        # Один такт простоя: проход и ни одного сообщения.
        _worker(_strict([(None, None, None)])).run(
            lambda body, kind: None, sweep=sweep, sweep_interval=0
        )
    finally:
        engine.dispose()

    finished = spans.get_finished_spans()
    (pass_span,) = [s for s in finished if s.name == "review.sweep"]
    assert pass_span.parent is None
    database = [s for s in finished if s.kind is SpanKind.CLIENT]
    assert database  # и зависшие прогоны, и сессии читаются запросами
    pass_context = pass_span.get_span_context()
    assert pass_context is not None
    pass_id = pass_context.span_id
    assert all(s.parent is not None and s.parent.span_id == pass_id for s in database)


# --- повтор упавших доставок вебхука (#149) ---------------------------------


def test_the_redelivery_step_runs_on_its_own_interval() -> None:
    clock = {"now": NOW}
    calls: list[datetime] = []

    def redeliver() -> int:
        calls.append(clock["now"])
        return 0

    maintenance = build_maintenance(
        FakeUnitOfWork,
        stale_limit=LIMIT,
        queued_limit=QUEUED_LIMIT,
        simulation_stale_limit=SIMULATION_LIMIT,
        now=lambda: clock["now"],
        redeliver=redeliver,
        redelivery_interval=timedelta(minutes=5),
    )
    for minute in range(11):
        clock["now"] = NOW + timedelta(minutes=minute)
        maintenance()

    assert calls == [NOW, NOW + timedelta(minutes=5), NOW + timedelta(minutes=10)]


def test_a_failing_redelivery_does_not_stop_the_other_steps(
    caplog, monkeypatch, spans: InMemorySpanExporter
) -> None:
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(logging.getLogger("app.worker.factory"), "disabled", False)
    uow = FakeUnitOfWork()
    uow.sessions.add(_session(1, NOW - timedelta(days=1)))

    def redeliver() -> int:
        raise RuntimeError("HTTP 429")

    with sweep_span():
        build_maintenance(
            lambda: uow,
            stale_limit=LIMIT,
            queued_limit=QUEUED_LIMIT,
            simulation_stale_limit=SIMULATION_LIMIT,
            now=lambda: NOW,
            redeliver=redeliver,
            redelivery_interval=timedelta(minutes=5),
        )()

    assert uow.sessions.stored == []
    (record,) = caplog.records
    assert record.maintenance_step == "redeliver_webhooks"
    (pass_span,) = [s for s in spans.get_finished_spans() if s.name == "review.sweep"]
    assert pass_span.status.status_code is StatusCode.ERROR


def _settings(**overrides: object) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="test-secret",
        **overrides,  # type: ignore[arg-type]
    )


class _Deliveries:
    def __init__(self, event: str = "pull_request", action: str = "synchronize") -> None:
        self.since: list[datetime] = []
        self.event = event
        self.action = action

    def list_recent(self, since: datetime) -> list[WebhookDelivery]:
        self.since.append(since)
        return [
            WebhookDelivery(
                id=7,
                guid="g",
                delivered_at=NOW - timedelta(hours=1),
                redelivery=False,
                status="Invalid HTTP Response: 502",
                status_code=502,
                event=self.event,
                action=self.action,
            )
        ]

    def delivery_payload(self, delivery_id: int) -> dict[str, object] | None:
        raise AssertionError("тест не читает payload")

    def redeliver(self, delivery_id: int) -> None:
        raise AssertionError("тест не переотправляет")


def _no_vcs() -> FakeVcs:
    raise AssertionError("шлюз VCS без реквизитов не собирается")


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"github_app_id": "42"},
        {"github_app_private_key": "pem"},
        {"github_app_id": "42", "github_app_private_key": "pem", "webhook_redelivery_enabled": False},
    ],
)
def test_without_app_credentials_or_when_disabled_redelivery_is_off(
    overrides: dict[str, object], caplog, monkeypatch
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(logging.getLogger("app.worker.factory"), "disabled", False)

    def unused() -> _Deliveries:
        raise AssertionError("адаптер без реквизитов не собирается")

    assert build_redelivery(_settings(**overrides), unused, _no_vcs, now=lambda: NOW) is None
    (record,) = [r for r in caplog.records if "повтор упавших доставок" in r.getMessage()]
    assert record.levelno == logging.INFO


def test_redelivery_uses_the_policy_from_settings(monkeypatch) -> None:
    hooks = _Deliveries()
    settings = _settings(
        github_app_id="42",
        github_app_private_key="pem",
        webhook_redelivery_max_attempts=2,
        webhook_redelivery_backoff_seconds=7200,
        webhook_redelivery_max_per_pass=3,
    )

    redeliver = build_redelivery(settings, lambda: hooks, FakeVcs, now=lambda: NOW)

    assert redeliver is not None
    # Пауза 2 часа, последняя попытка час назад — переотправлять рано.
    assert redeliver() == 0
    assert hooks.since == [NOW - REDELIVERY_WINDOW]


def test_redelivery_stops_on_the_pass_budget_from_settings() -> None:
    """Монотонные часы подменены: каждый вызов на 10 с позже, бюджет — 5 с."""
    hooks = _Deliveries(event="pull_request_review_thread", action="resolved")
    ticks = iter(float(10 * n) for n in range(10))
    settings = _settings(
        github_app_id="42",
        github_app_private_key="pem",
        webhook_redelivery_pass_budget_seconds=5,
    )

    redeliver = build_redelivery(
        settings, lambda: hooks, FakeVcs, now=lambda: NOW, monotonic=lambda: next(ticks)
    )

    assert redeliver is not None
    # Доставка к переотправке готова, но бюджет истёк до неё: `redeliver` фейка не вызван.
    assert redeliver() == 0


def test_stale_guids_are_remembered_between_passes() -> None:
    """Память устаревших `guid` живёт в замыкании шага: второй проход их не запрашивает."""

    class StaleDeliveries(_Deliveries):
        def __init__(self) -> None:
            super().__init__(event="pull_request", action="synchronize")
            self.payloads_read = 0

        def delivery_payload(self, delivery_id: int) -> dict[str, object] | None:
            self.payloads_read += 1
            return webhook_payload("synchronize")

    hooks = StaleDeliveries()
    vcs = FakeVcs()  # голова PR — коммит `opened`, доставка `synchronize` устарела
    settings = _settings(github_app_id="42", github_app_private_key="pem")
    redeliver = build_redelivery(settings, lambda: hooks, lambda: vcs, now=lambda: NOW)

    assert redeliver is not None
    assert (redeliver(), redeliver()) == (0, 0)
    assert hooks.payloads_read == 1
    assert len(vcs.calls) == 1
