"""Решение по сообщению после обработки (#157): ack, повтор или DLQ."""

import pytest

from app.application.review_pipeline import ReviewDeferred, ReviewNotStarted
from app.domain.llm_errors import LlmTimeoutError
from app.worker.handling import Decision, decide


def test_no_exception_means_ack() -> None:
    assert decide(None) is Decision.ACK


def test_a_run_not_started_is_retried() -> None:
    """Прогон не прочитан или не захвачен — он `queued`, и повтор его возьмёт."""
    assert decide(ReviewNotStarted("база недоступна")) is Decision.RETRY


@pytest.mark.parametrize(
    "exc",
    [
        ValueError("битое сообщение"),
        LookupError("прогона нет"),
        LlmTimeoutError("модель не ответила"),
        RuntimeError("сбой после захвата"),
    ],
    ids=["bad-message", "no-run", "after-capture-llm", "after-capture"],
)
def test_any_other_exception_goes_to_the_dead_letter_queue(exc: Exception) -> None:
    """Нет прогона — повтор его не найдёт; сбой после захвата — прогон уже `failed`."""
    assert decide(exc) is Decision.DEAD_LETTER


def test_a_deferred_run_is_returned_without_spending_a_delivery() -> None:
    """#294: провайдер на паузе breaker'а — не сбой: своё решение, не RETRY с тратой попытки."""
    assert decide(ReviewDeferred(30.0)) is Decision.DEFER
