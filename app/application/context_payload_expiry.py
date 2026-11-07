"""Стирание тел пакетов контекста по сроку хранения.

В теле пакета дословно лежит чужой исходный код (`docs/erd.md`): хранить его вечно — вопрос
защиты данных и места на диске. Тело нужно, пока по прогону разбираются, откуда замечание;
через `retention` его стирает воркер, метаданные — файлы, уровни, токены, дайджест — остаются,
и история прогона не удаляется (`review-data-model`).

Проход идёт вместе с остальной уборкой воркера раз в минуту и стирает не больше `batch` тел в
одной транзакции: короткая транзакция не держит блокировки строк долго, а хвост доберут
следующие проходы.
"""

from datetime import datetime, timedelta

from app.application.ports import UnitOfWork


def expire_context_payloads(
    uow: UnitOfWork, *, now: datetime, retention: timedelta, batch: int
) -> int:
    """Стереть тела пакетов старше `retention`, не больше `batch`; вернуть их число."""
    with uow as work:
        expired = work.context_payloads.expire_bodies(now - retention, now, batch)
        work.commit()
    return expired
