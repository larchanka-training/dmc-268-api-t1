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
from app.domain.enums import TERMINAL_STATUSES, ReviewRunStatus


def stale_limit_for(
    run: ReviewRun, limit: timedelta, *, queued_limit: timedelta
) -> timedelta:
    """Порог прогона: у ждущего в очереди свой, у остальных — `limit`.

    Прогресс `queued` — время создания, и хвост очереди длиннее `limit` не
    значит, что прогон завис. Совсем без порога `queued` нельзя: задача, не
    дошедшая до брокера, иначе заблокировала бы коммит навсегда.
    """
    return queued_limit if run.status is ReviewRunStatus.QUEUED else limit


def find_stale(
    runs: Iterable[ReviewRun], now: datetime, limit: timedelta, *, queued_limit: timedelta
) -> list[ReviewRun]:
    """Вернуть нетерминальные прогоны, не продвинувшиеся за свой порог."""
    return [
        run
        for run in runs
        if run.status not in TERMINAL_STATUSES
        and run.last_progress_at < now - stale_limit_for(run, limit, queued_limit=queued_limit)
    ]
