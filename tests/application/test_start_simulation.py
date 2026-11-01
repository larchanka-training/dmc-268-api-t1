"""Запуск симуляции: вход, лимиты, запись и постановка в очередь - на фейках."""

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import count
from uuid import UUID

import pytest

from app.application.use_cases.start_simulation import (
    NothingToReview,
    SimulationLimits,
    SimulationRequestInput,
    start_simulation,
)
from app.domain.entities import Simulation
from app.domain.enums import SimulationSourceKind, SimulationStatus
from app.domain.simulation import (
    QUEUE_UNAVAILABLE,
    Limited,
    fail_simulation,
    new_simulation,
)

from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.webhooks import FakeQueue

NOW = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
USER = UUID(int=7)
LIMITS = SimulationLimits(max_per_window=3, ttl=timedelta(days=1))
CODE = SimulationRequestInput(
    source_kind=SimulationSourceKind.CODE, source="x = 1\n", rules="", file_path="app/a.py"
)


def _new_id() -> Callable[[], UUID]:
    counter = count(100)
    return lambda: UUID(int=next(counter))


def _start(
    uow: FakeUnitOfWork, queue: FakeQueue, request: SimulationRequestInput = CODE, *, now: datetime = NOW
) -> Simulation | Limited | NothingToReview:
    return start_simulation(request, user_id=USER, uow=uow, queue=queue, limits=LIMITS, now=now, new_id=_new_id())


def _past(n: int, ago: timedelta) -> Simulation:
    started = new_simulation(
        simulation_id=UUID(int=n),
        user_id=USER,
        source_kind=SimulationSourceKind.CODE,
        source="y\n",
        rules="",
        file_path="app/b.py",
        now=NOW - ago,
        ttl=timedelta(days=1),
    )
    return fail_simulation(started, "LLM_TIMEOUT", NOW - ago).unwrap()


def test_a_launch_is_stored_queued_and_put_on_the_queue() -> None:
    uow, queue = FakeUnitOfWork(), FakeQueue()

    started = _start(uow, queue)

    assert isinstance(started, Simulation)
    assert (started.status, started.user_id, started.expires_at) == (
        SimulationStatus.QUEUED,
        USER,
        NOW + timedelta(days=1),
    )
    assert uow.simulations.get(started.id) == started
    (job,) = queue.simulations
    assert job.simulation_id == started.id and job.id != started.id
    assert queue.jobs == []


@pytest.mark.parametrize(
    "request_input",
    [
        replace(CODE, file_path="uv.lock"),
        SimulationRequestInput(SimulationSourceKind.DIFF, "это не дифф", rules=""),
        SimulationRequestInput(
            SimulationSourceKind.DIFF,
            "diff --git a/a.py b/b.py\nsimilarity index 100%\nrename from a.py\nrename to b.py\n",
            rules="",
        ),
    ],
    ids=["фрагмент lock-файла", "не дифф", "только переименование"],
)
def test_input_without_reviewable_hunks_is_refused_before_anything_is_stored(
    request_input: SimulationRequestInput,
) -> None:
    uow, queue = FakeUnitOfWork(), FakeQueue()

    assert _start(uow, queue, request_input) == NothingToReview()
    assert uow.simulations.stored == {} and queue.simulations == []


def test_an_unfinished_simulation_blocks_the_next_launch_for_the_poll_interval() -> None:
    uow, queue = FakeUnitOfWork(), FakeQueue()
    _start(uow, queue)

    assert _start(uow, queue) == Limited(5, active=True)
    assert len(uow.simulations.stored) == 1 and len(queue.simulations) == 1


def test_the_hourly_limit_counts_the_users_launches_in_the_window() -> None:
    uow, queue = FakeUnitOfWork(), FakeQueue()
    for n, minutes in enumerate((50, 30, 10), start=1):
        uow.simulations.add(_past(n, timedelta(minutes=minutes)))

    assert _start(uow, queue) == Limited(10 * 60, active=False)
    assert queue.simulations == []


def test_a_concurrent_launch_that_won_the_index_is_reported_as_active() -> None:
    """Проверка лимита прошла, а вставку опередил параллельный запуск того же пользователя."""
    uow, queue = FakeUnitOfWork(), FakeQueue()
    rival = new_simulation(
        simulation_id=UUID(int=1),
        user_id=USER,
        source_kind=SimulationSourceKind.CODE,
        source="y\n",
        rules="",
        file_path="app/b.py",
        now=NOW,
        ttl=timedelta(days=1),
    )
    uow.simulations.add(rival)
    # Чтение лимита ещё не видит соперника - его транзакция не закоммичена.
    uow.simulations.has_active = lambda user_id: False  # type: ignore[method-assign]

    assert _start(uow, queue) == Limited(5, active=True)
    assert list(uow.simulations.stored) == [rival.id]
    assert queue.simulations == []


def test_a_broker_failure_closes_the_simulation_at_once() -> None:
    uow, queue = FakeUnitOfWork(), FakeQueue(error=ConnectionError("брокер недоступен"))

    started = _start(uow, queue)

    assert isinstance(started, Simulation)
    assert (started.status, started.error_code) == (SimulationStatus.FAILED, QUEUE_UNAVAILABLE)
    assert (started.source, started.rules) == (None, None)
    assert uow.simulations.get(started.id) == started
    # Слот «одна незавершённая» свободен: следующий запуск не упирается в лимит.
    assert not uow.simulations.has_active(USER)
