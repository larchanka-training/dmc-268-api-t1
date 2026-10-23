"""Периодический проход воркера: зависшие прогоны и истёкшие сессии, без брокера.

Спан прохода проверяется на настоящей базе: нужны спаны запросов SQLAlchemy.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import create_engine

from app.domain.entities import ReviewRun, Session
from app.domain.enums import ReviewRunStatus, TriggerSource
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.telemetry import instrument_engine, sweep_span
from app.worker.factory import build_maintenance

from ..conftest import requires_db
from ..fakes.unit_of_work import FakeUnitOfWork
from .test_worker_loop import FakeChannel, _worker

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

    build_maintenance(lambda: uow, stale_limit=timedelta(minutes=30), now=lambda: NOW)()

    assert [s.id for s in uow.sessions.stored] == [UUID(int=2)]


LIMIT = timedelta(minutes=30)


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

    build_maintenance(lambda: uow, stale_limit=LIMIT, now=lambda: NOW)()

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

    build_maintenance(uow_factory, stale_limit=LIMIT, now=lambda: NOW)()

    assert calls == 2
    assert uow.sessions.stored == []
    assert "проход по зависшим прогонам завершился ошибкой" in caplog.text


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
        build_maintenance(uow_factory, stale_limit=LIMIT, now=lambda: NOW)()

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
        lambda: SqlAlchemyUnitOfWork(engine), stale_limit=LIMIT, now=lambda: NOW
    )
    try:
        # Один такт простоя: проход и ни одного сообщения.
        _worker(FakeChannel([(None, None, None)])).run(
            lambda body: None, sweep=sweep, sweep_interval=0
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
