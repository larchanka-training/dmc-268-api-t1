"""Запуск симуляции песочницы: проверка входа, лимиты, запись и постановка в очередь.

Вход без ревьюируемых hunk'ов отклоняется до записи: тот же разбор и фильтр,
что у ревью. Лимиты (design D4) считаются по строкам самой таблицы:
незавершённая симуляция и запуски за скользящий час; гонку двух параллельных
запусков закрывает уникальный индекс - вторая запись не проходит, и ответ тот же.

Задача не встала в очередь - симуляция сразу `failed` с `QUEUE_UNAVAILABLE`
(D7): ресурс создан, клиент увидит итог первым опросом, а слот «одна
незавершённая» свободен.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from app.application.ports import UnitOfWork
from app.application.ports.job_queue import JobQueue
from app.domain.entities import Simulation, SimulationJob
from app.domain.enums import SimulationSourceKind
from app.domain.simulation import (
    ACTIVE_RETRY_AFTER_SECONDS,
    QUEUE_UNAVAILABLE,
    Limited,
    admit_simulation,
    fail_simulation,
    has_reviewable_hunks,
    new_simulation,
    simulation_files,
)

logger = logging.getLogger(__name__)

LIMIT_WINDOW = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class SimulationRequestInput:
    """Вход пользователя: фрагмент кода с путём или дифф, и текст правил."""

    source_kind: SimulationSourceKind
    source: str
    rules: str
    file_path: str | None = None


@dataclass(frozen=True, slots=True)
class SimulationLimits:
    max_per_window: int
    ttl: timedelta


@dataclass(frozen=True, slots=True)
class NothingToReview:
    """После разбора и фильтра ревью в входе нет ни одного hunk'а."""


def start_simulation(
    request: SimulationRequestInput,
    *,
    user_id: UUID,
    uow: UnitOfWork,
    queue: JobQueue,
    limits: SimulationLimits,
    now: datetime,
    new_id: Callable[[], UUID],
) -> Simulation | Limited | NothingToReview:
    """Запустить симуляцию: записанная симуляция, отказ лимита или «нечего ревьюить»."""
    files = simulation_files(request.source_kind, request.source, request.file_path)
    if not has_reviewable_hunks(files):
        return NothingToReview()

    simulation = new_simulation(
        simulation_id=new_id(),
        user_id=user_id,
        source_kind=request.source_kind,
        source=request.source,
        rules=request.rules,
        file_path=request.file_path,
        now=now,
        ttl=limits.ttl,
    )
    with uow:
        admission = admit_simulation(
            active=uow.simulations.has_active(user_id),
            recent_created_at=uow.simulations.created_since(user_id, now - LIMIT_WINDOW),
            now=now,
            max_per_window=limits.max_per_window,
            window=LIMIT_WINDOW,
        )
        if isinstance(admission, Limited):
            return admission
        if not uow.simulations.add(simulation):
            # Параллельный запуск того же пользователя успел раньше.
            return Limited(ACTIVE_RETRY_AFTER_SECONDS, active=True)
        uow.commit()

    try:
        queue.enqueue(SimulationJob(id=new_id(), simulation_id=simulation.id))
    except Exception:
        logger.exception("задача симуляции %s не встала в очередь", simulation.id)
        failed = fail_simulation(simulation, QUEUE_UNAVAILABLE, now).unwrap()
        with uow:
            uow.simulations.update(failed)
            uow.commit()
        return failed
    return simulation
