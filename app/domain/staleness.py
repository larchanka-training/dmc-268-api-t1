"""Поиск прогонов, которые перестали продвигаться.

На коммит допускается не больше одного нетерминального прогона, поэтому
упавший посреди работы worker заблокировал бы этот коммит навсегда. Ключ в
Redis с TTL истёк бы сам; ограничение в базе — нет, поэтому уборка явная.

Выбор прогонов — чистая функция, `now` приходит аргументом. Переводить их в
failed — дело вызывающего кода, и оно достаётся тому worker'у, который и может
подвесить прогон.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from app.domain.entities import ReviewRun
from app.domain.enums import TERMINAL_STATUSES


def find_stale(
    runs: Iterable[ReviewRun], now: datetime, limit: timedelta
) -> list[ReviewRun]:
    """Вернуть нетерминальные прогоны, не продвинувшиеся за `limit`."""
    cutoff = now - limit
    return [
        run
        for run in runs
        if run.status not in TERMINAL_STATUSES and run.last_progress_at < cutoff
    ]
