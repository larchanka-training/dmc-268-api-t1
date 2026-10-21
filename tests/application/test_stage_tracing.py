"""Спаны этапов прогона через порт `StageTracer`: открываются и закрываются вместе с шагами.

Без OpenTelemetry, базы и сети: трассировщик — записывающий двойник порта. Что спан
OTel несёт `review_run_id` и ложится в трейс воркера — `tests/infrastructure/test_telemetry.py`
и сквозной `tests/test_tracing_e2e.py`.
"""

from dataclasses import dataclass, field
from uuid import UUID

import pytest

from app.application.ports.stage_tracer import StageSpan
from app.domain.enums import ReviewRunStage
from app.domain.llm_errors import LlmOutputError, LlmTimeoutError
from app.domain.vcs_errors import VcsError

from ..fakes.llm_gateway import ScriptedLlmGateway, review_response
from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.webhooks import FakeVcs
from .test_review_pipeline import JOB, STAGES, _RetryingOnce, _run, _seed_run, _steps

Event = tuple[str, ReviewRunStage, str | None]


@dataclass
class _RecordingTracer:
    """Пишет открытия и закрытия спанов; `open` — сколько открыто сейчас."""

    events: list[Event] = field(default_factory=list)
    run_ids: set[UUID] = field(default_factory=set)
    open: int = 0

    def start(self, review_run_id: UUID, stage: ReviewRunStage) -> StageSpan:
        self.run_ids.add(review_run_id)
        self.events.append(("start", stage, None))
        self.open += 1
        tracer = self

        class _Span:
            def end(self, error_code: str | None) -> None:
                tracer.events.append(("end", stage, error_code))
                tracer.open -= 1

        return _Span()


def _spans(tracer: _RecordingTracer) -> list[tuple[ReviewRunStage, str | None]]:
    """Закрытые спаны по порядку: этап и код ошибки."""
    return [(stage, code) for kind, stage, code in tracer.events if kind == "end"]


def test_every_step_is_a_span_of_its_run_closed_in_order() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    tracer = _RecordingTracer()

    _run(uow, ScriptedLlmGateway([review_response()]), stage_tracer=tracer)

    assert tracer.events == [
        event
        for stage in STAGES
        for event in (("start", stage, None), ("end", stage, None))
    ]
    assert tracer.run_ids == {JOB.review_run_id}
    assert tracer.open == 0


def test_a_failed_stage_closes_its_span_with_the_step_error_code() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    tracer = _RecordingTracer()

    with pytest.raises(VcsError):
        _run(
            uow,
            ScriptedLlmGateway([]),
            vcs_gateway=FakeVcs(error=VcsError("GitHub недоступен")),
            stage_tracer=tracer,
        )

    assert _spans(tracer) == [(ReviewRunStage.FETCHING_DIFF, "SCM_UNAVAILABLE")]
    assert tracer.open == 0


def test_a_retry_is_a_failed_span_and_a_new_span_of_the_same_stage() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    tracer = _RecordingTracer()
    gateway = _RetryingOnce(
        ScriptedLlmGateway([review_response()]), LlmOutputError("не по схеме")
    )

    _run(uow, gateway, stage_tracer=tracer)

    assert _spans(tracer)[2:4] == [
        (ReviewRunStage.ANALYSING, "LLM_INVALID_OUTPUT"),
        (ReviewRunStage.ANALYSING, None),
    ]
    # Спаны повторяют шаги в базе один к одному.
    assert [(stage, code) for stage, _, _, code in _steps(uow)] == _spans(tracer)


def test_a_model_failure_leaves_no_span_open() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)
    tracer = _RecordingTracer()

    with pytest.raises(LlmTimeoutError):
        _run(
            uow,
            ScriptedLlmGateway([LlmTimeoutError("таймаут", tokens_used=30)]),
            stage_tracer=tracer,
        )

    assert _spans(tracer)[-1] == (ReviewRunStage.ANALYSING, "LLM_TIMEOUT")
    assert tracer.open == 0


def test_without_a_tracer_steps_are_recorded_as_before() -> None:
    uow = FakeUnitOfWork()
    _seed_run(uow)

    _run(uow, ScriptedLlmGateway([review_response()]))

    assert [stage for stage, _, _, _ in _steps(uow)] == STAGES
