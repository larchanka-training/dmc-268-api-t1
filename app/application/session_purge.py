"""Удаление истёкших сессий.

Истёкшая сессия уже не пускает: `authenticate` отказывает ей и удаляет при
следующем обращении. Но к сессии, с которой больше не приходят, обращения не
будет, и строка лежала бы вечно. Поэтому воркер раз в минуту стирает такие
строки. Индекса по `expires_at` нет: таблица мала (сессия живёт 30 дней), а
проход идёт раз в минуту и не лежит на пути запроса.
"""

from datetime import datetime

from app.application.ports import UnitOfWork


def purge_expired_sessions(uow: UnitOfWork, *, now: datetime) -> int:
    """Удалить сессии с истёкшим сроком; вернуть их число."""
    with uow as work:
        purged = work.sessions.delete_expired(now)
        work.commit()
    return purged
