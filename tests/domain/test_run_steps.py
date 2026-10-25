"""Шаги прогона: начало, исход и код сбоя. Ни часов, ни базы."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from app.domain.enums import ReviewRunStage, ReviewRunStepStatus
from app.domain.llm_errors import (
    LlmError,
    LlmOutputError,
    LlmRateLimitedError,
    LlmTimeoutError,
    PromptTooLargeError,
)
from app.domain.run_steps import (
    ABANDONED,
    INTERNAL_ERROR,
    abandon_steps,
    error_code,
    finish_step,
    start_step,
)
from app.domain.vcs_errors import DiffTooLargeError, VcsError, VcsFileTooLargeError

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
RUN_ID, STEP_ID = UUID(int=1), UUID(int=2)


def _started():
    return start_step(RUN_ID, ReviewRunStage.ANALYSING, started_at=NOW, step_id=STEP_ID)


def test_a_started_step_is_running_without_an_outcome() -> None:
    step = _started()

    assert step.id == STEP_ID
    assert step.review_run_id == RUN_ID
    assert step.stage is ReviewRunStage.ANALYSING
    assert step.status is ReviewRunStepStatus.RUNNING
    assert step.started_at == NOW
    assert (step.duration_seconds, step.tokens_used, step.error_code) == (
        None,
        None,
        None,
    )


def test_a_step_without_an_error_code_completes() -> None:
    step = finish_step(_started(), duration_seconds=2.5, tokens_used=120)

    assert step.status is ReviewRunStepStatus.COMPLETED
    assert step.duration_seconds == 2.5
    assert step.tokens_used == 120
    assert step.error_code is None
    assert (step.started_at, step.stage) == (NOW, ReviewRunStage.ANALYSING)


def test_a_step_with_an_error_code_fails() -> None:
    step = finish_step(
        _started(), duration_seconds=1.0, tokens_used=None, error_code="LLM_TIMEOUT"
    )

    assert step.status is ReviewRunStepStatus.FAILED
    assert step.error_code == "LLM_TIMEOUT"
    assert step.tokens_used is None


def test_an_empty_error_code_still_fails_the_step() -> None:
    # Как CHECK в базе: упавший — шаг с кодом не `None`, а не с непустым кодом.
    step = finish_step(_started(), duration_seconds=1.0, tokens_used=None, error_code="")

    assert step.status is ReviewRunStepStatus.FAILED
    assert step.error_code == ""


def test_a_negative_duration_is_clamped_to_zero() -> None:
    assert (
        finish_step(
            _started(), duration_seconds=-0.1, tokens_used=None
        ).duration_seconds
        == 0.0
    )


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (LlmError("модель недоступна"), "LLM_UNAVAILABLE"),
        (LlmTimeoutError("таймаут"), "LLM_TIMEOUT"),
        (LlmRateLimitedError("лимит"), "LLM_RATE_LIMITED"),
        (LlmOutputError("не по схеме"), "LLM_INVALID_OUTPUT"),
        (PromptTooLargeError("больше окна"), "PROMPT_TOO_LARGE"),
        (VcsError("GitHub недоступен"), "SCM_UNAVAILABLE"),
        (VcsFileTooLargeError("слишком большой"), "SCM_UNAVAILABLE"),
        (DiffTooLargeError("дифф больше лимита"), "DIFF_TOO_LARGE"),
        (RuntimeError("ошибка программы"), INTERNAL_ERROR),
    ],
    ids=["llm", "timeout", "rate-limited", "output", "too-large", "vcs", "vcs-subclass", "diff-too-large", "other"],
)
def test_the_error_code_comes_from_the_failure_class(
    error: Exception, code: str
) -> None:
    assert error_code(error) == code


def test_abandoned_running_steps_fail_as_stale_with_the_wall_clock_duration() -> None:
    running = _started()
    done = finish_step(
        start_step(RUN_ID, ReviewRunStage.FETCHING_DIFF, started_at=NOW, step_id=UUID(int=3)),
        duration_seconds=1.0,
        tokens_used=None,
    )

    (closed,) = abandon_steps([done, running], now=NOW + timedelta(minutes=2))

    assert closed.id == STEP_ID
    assert closed.status is ReviewRunStepStatus.FAILED
    assert closed.error_code == ABANDONED == "REVIEW_RUN_STALE"
    assert closed.duration_seconds == 120.0
    assert closed.tokens_used is None


def test_an_abandoned_step_with_a_known_outcome_is_closed_with_it() -> None:
    running = _started()
    other = start_step(RUN_ID, ReviewRunStage.ANALYSING, started_at=NOW, step_id=UUID(int=4))
    outcome = finish_step(running, duration_seconds=7.0, tokens_used=12, error_code="LLM_TIMEOUT")

    closed = abandon_steps(
        [running, other], now=NOW + timedelta(minutes=1), known={STEP_ID: outcome}
    )

    assert closed[0] == outcome
    assert closed[1].error_code == ABANDONED


def test_an_abandoned_step_never_gets_a_negative_duration() -> None:
    (closed,) = abandon_steps([_started()], now=NOW - timedelta(seconds=5))

    assert closed.duration_seconds == 0.0
