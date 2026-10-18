"""Удаление истёкших сессий — фейковый `UnitOfWork`, без БД."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.session_purge import purge_expired_sessions
from app.domain.entities import Session

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


def test_only_expired_sessions_are_deleted_and_counted() -> None:
    uow = FakeUnitOfWork()
    uow.sessions.add(_session(1, NOW - timedelta(seconds=1)))
    uow.sessions.add(_session(2, NOW - timedelta(days=3)))
    uow.sessions.add(_session(3, NOW + timedelta(days=1)))

    purged = purge_expired_sessions(uow, now=NOW)

    assert purged == 2
    assert [s.id for s in uow.sessions.stored] == [UUID(int=3)]


def test_a_session_expiring_exactly_now_is_expired() -> None:
    uow = FakeUnitOfWork()
    uow.sessions.add(_session(1, NOW))

    assert purge_expired_sessions(uow, now=NOW) == 1
