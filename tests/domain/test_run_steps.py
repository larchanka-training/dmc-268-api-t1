"""Шаги прогона: начало, исход и код сбоя. Ни часов, ни базы."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.enums import ReviewRunStage, ReviewRunStepStatus
from app.domain.llm_errors import (
    LlmError,
    LlmOutputError,
    LlmTimeoutError,
    PromptTooLargeError,
)
from app.domain.run_steps import INTERNAL_ERROR, error_code, finish_step, start_step
from app.domain.vcs_errors import VcsError, VcsFileTooLargeError

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
        (LlmOutputError("не по схеме"), "LLM_INVALID_OUTPUT"),
        (PromptTooLargeError("больше окна"), "PROMPT_TOO_LARGE"),
        (VcsError("GitHub недоступен"), "SCM_UNAVAILABLE"),
        (VcsFileTooLargeError("слишком большой"), "SCM_UNAVAILABLE"),
        (RuntimeError("ошибка программы"), INTERNAL_ERROR),
    ],
    ids=["llm", "timeout", "output", "too-large", "vcs", "vcs-subclass", "other"],
)
def test_the_error_code_comes_from_the_failure_class(
    error: Exception, code: str
) -> None:
    assert error_code(error) == code
