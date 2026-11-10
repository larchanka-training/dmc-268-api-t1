"""Уборка симуляций песочницы в проходе обслуживания воркера (design D8).

Незавершённая симуляция без смены статуса дольше порога - `failed` с
`SIMULATION_STALE`: воркер умер посреди анализа, задачу в окне деплоя взял
старый воркер или она слишком долго ждёт в очереди за ревью. Перевод
освобождает слот лимита «одна незавершённая» и стирает вход пользователя.
Симуляция с прошедшим `expires_at` удаляется вместе с результатом.
"""

from datetime import datetime, timedelta

from app.application.ports import UnitOfWork


def sweep_stale_simulations(uow: UnitOfWork, *, now: datetime, limit: timedelta) -> int:
    """Перевести в `failed` симуляции без смены статуса дольше `limit`; вернуть их число."""
    with uow as work:
        failed = work.simulations.fail_stale(now - limit, now)
        work.commit()
    return failed


def purge_expired_simulations(uow: UnitOfWork, *, now: datetime) -> int:
    """Удалить симуляции, срок хранения которых истёк; вернуть их число."""
    with uow as work:
        purged = work.simulations.delete_expired(now)
        work.commit()
    return purged
