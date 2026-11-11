"""Машина состояний breaker'а провайдера (#294): чистые функции над записью и временем."""

import pytest

from app.domain.circuit_breaker import (
    CLOSED,
    Breaker,
    BreakerPolicy,
    BreakerState,
    answered,
    failed,
    retry_after,
    state,
)

POLICY = BreakerPolicy(failure_threshold=3, pause_seconds=60.0, max_pause_seconds=600.0)


def _fail(breaker: Breaker, times: int, now: float) -> Breaker:
    for _ in range(times):
        breaker = failed(breaker, POLICY, now)
    return breaker


def test_failures_below_the_threshold_keep_it_closed() -> None:
    breaker = _fail(CLOSED, 2, now=100.0)

    assert state(breaker, 100.0) is BreakerState.CLOSED
    assert retry_after(breaker, 100.0) is None
    assert breaker.failures == 2


def test_the_threshold_opens_it_for_the_pause() -> None:
    breaker = _fail(CLOSED, 3, now=100.0)

    assert state(breaker, 100.0) is BreakerState.OPEN
    assert breaker.open_until == 160.0
    assert retry_after(breaker, 130.0) == 30.0


def test_an_answer_resets_the_count() -> None:
    """Отказы считаются подряд: ответ между ними обнуляет счёт."""
    breaker = _fail(answered(_fail(CLOSED, 2, now=0.0)), 2, now=0.0)

    assert state(breaker, 0.0) is BreakerState.CLOSED


def test_after_the_pause_it_is_half_open_and_lets_a_call_through() -> None:
    breaker = _fail(CLOSED, 3, now=100.0)

    assert state(breaker, 160.0) is BreakerState.HALF_OPEN
    assert retry_after(breaker, 160.0) is None


def test_an_answer_in_half_open_closes_it() -> None:
    breaker = answered(_fail(CLOSED, 3, now=100.0))

    assert breaker == CLOSED
    assert state(breaker, 160.0) is BreakerState.CLOSED


def test_a_failed_probe_reopens_it_with_a_doubled_pause() -> None:
    """Один отказ пробного вызова — снова пауза, вдвое длиннее: порог второй раз не копится."""
    breaker = failed(_fail(CLOSED, 3, now=100.0), POLICY, 160.0)

    assert state(breaker, 160.0) is BreakerState.OPEN
    assert breaker.open_until == 160.0 + 120.0
    assert breaker.trips == 2


def test_the_pause_stops_growing_at_the_ceiling() -> None:
    breaker = _fail(CLOSED, 3, now=0.0)
    now = 0.0
    for _ in range(10):
        assert breaker.open_until is not None
        now = breaker.open_until
        breaker = failed(breaker, POLICY, now)

    assert breaker.open_until == now + 600.0


def test_a_failure_during_the_pause_does_not_extend_it() -> None:
    """Вызов, начатый до размыкания, может отказать уже на паузе: пауза та же."""
    opened = _fail(CLOSED, 3, now=100.0)

    assert failed(opened, POLICY, 110.0).open_until == opened.open_until


@pytest.mark.parametrize(
    ("threshold", "pause", "ceiling"),
    [(0, 60.0, 600.0), (3, 0.0, 600.0), (3, 700.0, 600.0)],
    ids=["no-threshold", "no-pause", "pause-over-ceiling"],
)
def test_a_policy_that_cannot_work_is_rejected(threshold: int, pause: float, ceiling: float) -> None:
    with pytest.raises(ValueError, match="breaker"):
        BreakerPolicy(failure_threshold=threshold, pause_seconds=pause, max_pause_seconds=ceiling)
