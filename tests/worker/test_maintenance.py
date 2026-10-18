"""Периодический проход воркера: зависшие прогоны и истёкшие сессии, без брокера."""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.domain.entities import ReviewRun, Session
from app.domain.enums import ReviewRunStatus, TriggerSource
from app.worker.factory import build_maintenance

from ..fakes.unit_of_work import FakeUnitOfWork

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
