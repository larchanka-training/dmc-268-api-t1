"""Стирание тел пакетов контекста по сроку хранения — фейковый `UnitOfWork`, без БД."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.context_payload_expiry import expire_context_payloads
from app.domain.entities import ContextPayload, StoredContextPayload

from ..fakes.unit_of_work import FakeUnitOfWork

NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
RETENTION = timedelta(days=30)


def _payload(n: int, age: timedelta) -> ContextPayload:
    return ContextPayload(
        id=UUID(int=n),
        review_run_id=UUID(int=100),
        chunk_index=n,
        tiers=("diff",),
        file_paths=("app/main.py",),
        token_count=10,
        content_sha256=f"d{n}",
        body={"diff": "+secret code"},
        created_at=NOW - age,
        updated_at=NOW - age,
    )


def _stored(uow: FakeUnitOfWork) -> dict[UUID, StoredContextPayload]:
    return {p.id: p for p in uow.context_payloads.list_for_run(UUID(int=100))}


def test_only_bodies_older_than_retention_are_expired() -> None:
    uow = FakeUnitOfWork()
    uow.context_payloads.add(_payload(1, timedelta(days=31)))
    uow.context_payloads.add(_payload(2, timedelta(days=29)))

    expired = expire_context_payloads(uow, now=NOW, retention=RETENTION, batch=10)

    assert expired == 1
    old, fresh = _stored(uow)[UUID(int=1)], _stored(uow)[UUID(int=2)]
    assert (old.body, old.body_expired_at) == (None, NOW)
    assert (old.file_paths, old.token_count, old.content_sha256) == (("app/main.py",), 10, "d1")
    assert (fresh.body, fresh.body_expired_at) == ({"diff": "+secret code"}, None)


def test_a_payload_exactly_at_the_retention_boundary_is_kept() -> None:
    uow = FakeUnitOfWork()
    uow.context_payloads.add(_payload(1, RETENTION))

    assert expire_context_payloads(uow, now=NOW, retention=RETENTION, batch=10) == 0


def test_one_pass_expires_at_most_a_batch_oldest_first() -> None:
    uow = FakeUnitOfWork()
    for n, days in enumerate((40, 90, 60), start=1):
        uow.context_payloads.add(_payload(n, timedelta(days=days)))

    assert expire_context_payloads(uow, now=NOW, retention=RETENTION, batch=2) == 2

    expired = {p.id for p in _stored(uow).values() if p.body_expired_at is not None}
    assert expired == {UUID(int=2), UUID(int=3)}
    assert expire_context_payloads(uow, now=NOW, retention=RETENTION, batch=2) == 1


def test_an_already_expired_body_is_not_touched_again() -> None:
    uow = FakeUnitOfWork()
    earlier = NOW - timedelta(days=5)
    uow.context_payloads.add(_payload(1, timedelta(days=60)))
    (stored,) = uow.context_payloads.snapshot()
    uow.context_payloads.restore([replace(stored, body=None, body_expired_at=earlier)])

    assert expire_context_payloads(uow, now=NOW, retention=RETENTION, batch=10) == 0
    assert _stored(uow)[UUID(int=1)].body_expired_at == earlier
