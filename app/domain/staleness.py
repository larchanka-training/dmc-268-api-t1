"""Finding runs that stopped making progress.

At most one non-terminal run may exist per commit, so a worker that dies
mid-run would block that commit forever. A Redis key with a TTL would have
expired on its own; a database constraint does not, so the sweep is explicit.

Selecting the runs is pure and takes `now` as an argument. Failing them is the
caller's job, and lands with the worker that can actually strand one.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from app.domain.entities import ReviewRun
from app.domain.enums import TERMINAL_STATUSES


def find_stale(
    runs: Iterable[ReviewRun], now: datetime, limit: timedelta
) -> list[ReviewRun]:
    """Return the non-terminal runs that have not advanced within `limit`."""
    cutoff = now - limit
    return [
        run
        for run in runs
        if run.status not in TERMINAL_STATUSES and run.last_progress_at < cutoff
    ]
