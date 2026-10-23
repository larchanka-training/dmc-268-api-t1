"""Малый нагрузочный прогон в каждом CI: корректность под конкуренцией, без порогов времени.

50 одновременных вебхуков из `docs/testing/TEST_PLAN.md` §3.6 плюс повторные
доставки вперемешку с первыми, четыре воркера на одной очереди. Время на
раннере GitHub шумит, поэтому здесь проверяется только то, что не зависит от
скорости машины: ни одного 5xx, ровно один прогон и одно ревью на PR, все
прогоны завершены, очереди пусты.
"""

import pytest

from ..conftest import requires_broker, requires_db
from ..e2e.stack import Stack
from .checks import assert_consistent
from .scenario import LoadProfile, run_load, summarize

pytestmark = [pytest.mark.integration, requires_db, requires_broker]


def test_fifty_concurrent_webhooks_with_redeliveries_and_four_workers(stack: Stack) -> None:
    profile = LoadProfile(
        pull_requests=50,
        redeliveries=15,
        senders=16,
        workers=4,
        hold_workers_until_intake_done=True,
    )

    result = run_load(stack, profile)

    assert_consistent(summarize(result), strict=True)
    assert len(result.deliveries) == 65
