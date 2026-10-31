"""Сигнал изменений прогонов: `pg_notify` в адаптере и `LISTEN` в API — на настоящей базе."""

import asyncio
import datetime as dt
import gc
from dataclasses import replace

import pytest
from sqlalchemy import text

from app.application.ports.run_changes import RunChangesLost
from app.domain.entities import MergeRequest, Repository, ReviewRun
from app.domain.enums import MergeRequestState, Provider, ReviewRunStatus, TriggerSource
from app.domain.ids import new_id
from app.infrastructure.db.run_changes import (
    LISTENER_APPLICATION_NAME,
    PostgresRunChanges,
    listen_conninfo,
)
from app.infrastructure.db.run_changes import (
    _Subscription as Subscription,
)
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import TEST_DATABASE_URL, requires_db

pytestmark = [pytest.mark.integration, requires_db]

T0 = dt.datetime(2026, 10, 24, 12, 0, tzinfo=dt.UTC)
# Сколько ждать уведомления: оно приходит за миллисекунды, запас — на медленный CI.
WAIT = 2.0


class Boom(Exception):
    pass


@pytest.fixture
def merge_request(clean_db) -> MergeRequest:
    repository = Repository(
        id=new_id(),
        provider=Provider.GITHUB,
        provider_id="1",
        full_name="acme/api",
        default_branch="main",
        auto_review_enabled=True,
        created_at=T0,
        updated_at=T0,
        connected_at=T0,
    )
    merge_request = MergeRequest(
        id=new_id(),
        repository_id=repository.id,
        number=1,
        title="t",
        description="",
        author="a",
        source_branch="feat",
        target_branch="main",
        head_sha="abc",
        state=MergeRequestState.OPEN,
        created_at=T0,
        updated_at=T0,
    )
    with SqlAlchemyUnitOfWork(clean_db) as uow:
        uow.repositories.add(repository)
        uow.commit()
    with SqlAlchemyUnitOfWork(clean_db) as uow:
        uow.merge_requests.add(merge_request)
        uow.commit()
    return merge_request


def _run(merge_request: MergeRequest) -> ReviewRun:
    return ReviewRun(
        id=new_id(),
        merge_request_id=merge_request.id,
        head_sha="abc",
        status=ReviewRunStatus.QUEUED,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=T0,
        created_at=T0,
        updated_at=T0,
    )


def _listener() -> PostgresRunChanges:
    assert TEST_DATABASE_URL is not None
    return PostgresRunChanges(listen_conninfo(TEST_DATABASE_URL), reconnect_delay=0.1)


def test_a_committed_run_write_signals_and_a_rolled_back_one_does_not(clean_db, merge_request) -> None:
    run = _run(merge_request)

    def add_and_roll_back() -> None:
        with pytest.raises(Boom), SqlAlchemyUnitOfWork(clean_db) as uow:
            uow.review_runs.add(run)
            raise Boom

    def add_and_commit() -> None:
        with SqlAlchemyUnitOfWork(clean_db) as uow:
            uow.review_runs.add(run)
            uow.commit()

    def update_and_commit() -> None:
        with SqlAlchemyUnitOfWork(clean_db) as uow:
            uow.review_runs.update(replace(run, status=ReviewRunStatus.BUILDING_CONTEXT))
            uow.commit()

    async def scenario() -> tuple[bool, bool, bool]:
        changes = _listener()
        try:
            subscription = await changes.subscribe()
            await asyncio.to_thread(add_and_roll_back)
            after_rollback = await subscription.wait(0.5)
            await asyncio.to_thread(add_and_commit)
            after_add = await subscription.wait(WAIT)
            await asyncio.to_thread(update_and_commit)
            after_update = await subscription.wait(WAIT)
            return after_rollback, after_add, after_update
        finally:
            await changes.close()

    assert asyncio.run(scenario()) == (False, True, True)


def test_a_dropped_listen_connection_loses_subscriptions_and_reconnects(clean_db, merge_request) -> None:
    """Обрыв LISTEN: текущие подписки — `RunChangesLost`, новая подписка снова получает сигналы."""

    def kill_listener() -> None:
        with clean_db.connect() as conn:
            conn.execute(
                text("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = :name"),
                {"name": LISTENER_APPLICATION_NAME},
            )
            conn.commit()

    def add_and_commit() -> None:
        with SqlAlchemyUnitOfWork(clean_db) as uow:
            uow.review_runs.add(_run(merge_request))
            uow.commit()

    async def scenario() -> tuple[bool, bool]:
        changes = _listener()
        try:
            before = await changes.subscribe()
            await asyncio.to_thread(kill_listener)
            try:
                await before.wait(WAIT)
                lost = False
            except RunChangesLost:
                lost = True
            after = await changes.subscribe()
            await asyncio.to_thread(add_and_commit)
            return lost, await after.wait(WAIT)
        finally:
            await changes.close()

    assert asyncio.run(scenario()) == (True, True)


def test_an_unreachable_database_refuses_the_subscription() -> None:
    changes = PostgresRunChanges(
        "postgresql://nobody:nothing@127.0.0.1:1/none", connect_timeout=0.5, reconnect_delay=0.1
    )

    async def scenario() -> None:
        try:
            await changes.subscribe()
        finally:
            await changes.close()

    with pytest.raises(RunChangesLost):
        asyncio.run(scenario())



def test_an_abandoned_subscription_does_not_pile_up(clean_db) -> None:
    """Подписка, брошенная без `close()`, уходит вместе со ссылкой на неё, а не копится до обрыва LISTEN."""

    async def scenario() -> int:
        changes = _listener()
        try:
            await changes.subscribe()  # ссылку никто не держит — как генератор, который не начал итерироваться
            gc.collect()
            return len(changes._subscribers)
        finally:
            await changes.close()

    assert asyncio.run(scenario()) == 0


def test_a_zero_timeout_still_sees_a_signal_and_a_loss() -> None:
    """Дочитывание пачки ждёт с нулевым таймаутом: пришедший сигнал и обрыв должны быть видны и тогда."""
    changes = _listener()

    async def scenario() -> tuple[bool, bool, bool]:
        signalled, quiet, lost = Subscription(changes), Subscription(changes), Subscription(changes)
        signalled.signal()
        lost.lose()
        try:
            await lost.wait(0.0)
            saw_loss = False
        except RunChangesLost:
            saw_loss = True
        return await signalled.wait(0.0), await quiet.wait(0.0), saw_loss

    assert asyncio.run(scenario()) == (True, False, True)
