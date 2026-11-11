"""Breaker провайдера с часами и решение воркера, ждать ли провайдеров (#294)."""

import logging

import pytest

from app.application.circuit_breaker import CircuitBreaker, deferral
from app.domain.circuit_breaker import BreakerPolicy, BreakerState

POLICY = BreakerPolicy(failure_threshold=2, pause_seconds=60.0, max_pause_seconds=600.0)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _breaker(clock: Clock, name: str = "GitHub") -> CircuitBreaker:
    return CircuitBreaker(name, POLICY, clock=clock)


def _open(breaker: CircuitBreaker) -> CircuitBreaker:
    breaker.record_failure()
    breaker.record_failure()
    return breaker


def test_it_opens_on_the_threshold_and_half_opens_after_the_pause() -> None:
    clock = Clock()
    breaker = _open(_breaker(clock))

    assert breaker.state() is BreakerState.OPEN
    assert breaker.retry_after() == 60.0
    clock.now += 60.0
    assert breaker.state() is BreakerState.HALF_OPEN
    assert breaker.retry_after() is None
    breaker.record_answer()
    assert breaker.state() is BreakerState.CLOSED


def test_opening_and_closing_are_logged_with_the_provider(caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    with caplog.at_level(logging.INFO, logger="app.application.circuit_breaker"):
        breaker = _open(_breaker(clock))
        breaker.record_answer()

    opened, closed = caplog.records
    assert opened.levelno == logging.WARNING and "разомкнут" in opened.getMessage()
    assert closed.levelno == logging.INFO and "замкнут" in closed.getMessage()
    assert opened.__dict__["provider"] == closed.__dict__["provider"] == "GitHub"


def test_an_answer_of_a_closed_breaker_is_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.application.circuit_breaker"):
        _breaker(Clock()).record_answer()

    assert caplog.records == []


def test_no_breakers_never_defer() -> None:
    assert deferral(None, []) is None


def test_github_paused_defers_until_its_probe() -> None:
    clock = Clock()
    assert deferral(_open(_breaker(clock)), [_breaker(clock, "Ollama")]) == 60.0


def test_one_model_provider_paused_does_not_defer_while_another_answers() -> None:
    """Фоллбэк пропускает провайдера на паузе: прогону хватает второго."""
    clock = Clock()
    llm = [_open(_breaker(clock, "Ollama")), _breaker(clock, "Eurorouter")]

    assert deferral(_breaker(clock), llm) is None


def test_every_model_provider_paused_defers_until_the_first_probe() -> None:
    clock = Clock()
    ollama = _open(_breaker(clock, "Ollama"))
    clock.now += 20.0
    eurorouter = _open(_breaker(clock, "Eurorouter"))

    assert deferral(_breaker(clock), [ollama, eurorouter]) == 40.0


def test_both_github_and_the_model_paused_defer_until_both_can_be_probed() -> None:
    clock = Clock()
    ollama = _open(_breaker(clock, "Ollama"))
    clock.now += 20.0
    github = _open(_breaker(clock))

    assert deferral(github, [ollama]) == 60.0
