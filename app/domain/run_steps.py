"""Шаги прогона: начало, исход и код сбоя. Ни часов, ни базы.

Шаг — данные продукта для «хода прогона» в кабинете, а не телеметрия: этап,
состояние, начало, длительность, токены и код ошибки. Время и
идентификаторы приходят аргументами, поэтому тест сверяет точные значения.
"""

from dataclasses import replace
from datetime import datetime
from uuid import UUID

from app.domain.entities import ReviewRunStep
from app.domain.enums import ReviewRunStage, ReviewRunStepStatus
from app.domain.llm_errors import LlmError
from app.domain.vcs_errors import VcsError

# Код сбоя, у которого нет своего класса: ошибка программы, а не внешней системы.
INTERNAL_ERROR = "INTERNAL_ERROR"


def start_step(
    review_run_id: UUID, stage: ReviewRunStage, *, started_at: datetime, step_id: UUID
) -> ReviewRunStep:
    """Шаг, который только что начался: длительности, токенов и кода ещё нет."""
    return ReviewRunStep(
        id=step_id,
        review_run_id=review_run_id,
        stage=stage,
        status=ReviewRunStepStatus.RUNNING,
        started_at=started_at,
    )


def finish_step(
    step: ReviewRunStep,
    *,
    duration_seconds: float,
    tokens_used: int | None,
    error_code: str | None = None,
) -> ReviewRunStep:
    """Закрыть шаг: с кодом сбоя — `failed`, без него — `completed`.

    Отрицательная длительность (часы не монотонны) приводится к нулю: шаг
    не может закончиться раньше, чем начался.
    """
    return replace(
        step,
        status=ReviewRunStepStatus.FAILED
        if error_code
        else ReviewRunStepStatus.COMPLETED,
        duration_seconds=max(duration_seconds, 0.0),
        tokens_used=tokens_used,
        error_code=error_code,
    )


def error_code(error: BaseException) -> str:
    """Код сбоя шага из каталога ошибок прогона (§8 PIPELINE_SPEC.md).

    Код несут классы сбоев модели и VCS; всё остальное — ошибка программы.
    """
    if isinstance(error, LlmError | VcsError):
        return error.code
    return INTERNAL_ERROR
