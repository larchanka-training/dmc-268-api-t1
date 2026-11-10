"""Порт трассировки этапов прогона: спан на каждый шаг, который пишет `StepRecorder`.

Спан — эксплуатационная телеметрия для команды, а не данные продукта: шаги, которые
видит пользователь, `StepRecorder` пишет в базу, и ни один ответ API из спанов не
строится (спека `observability`). Порт нужен ради границы слоя: слой приложения не
знает OpenTelemetry, адаптер живёт в `app/infrastructure/telemetry.py`. Без адаптера
`StepRecorder` берёт `NO_STAGE_TRACING` — спанов нет, шаги пишутся как прежде.
"""

from typing import Protocol
from uuid import UUID

from app.domain.enums import ReviewRunStage, SimulationStatus


class StageSpan(Protocol):
    def end(self, error_code: str | None) -> None:
        """Закрыть спан этапа; с `error_code` — упавшим."""
        ...


class StageTracer(Protocol):
    def start(self, review_run_id: UUID, stage: ReviewRunStage) -> StageSpan:
        """Открыть спан этапа прогона; он текущий, пока его не закроют."""
        ...


class SimulationTracer(Protocol):
    def start(self, simulation_id: UUID, stage: SimulationStatus) -> StageSpan:
        """Открыть спан этапа симуляции песочницы; он текущий, пока его не закроют.

        Атрибуты - идентификатор симуляции и этап, без кода и правил (design D9).
        """
        ...


class _NoStageSpan:
    def end(self, error_code: str | None) -> None:
        return None


class _NoStageTracing:
    def start(self, review_run_id: UUID, stage: ReviewRunStage) -> StageSpan:
        return _NoStageSpan()


class _NoSimulationTracing:
    def start(self, simulation_id: UUID, stage: SimulationStatus) -> StageSpan:
        return _NoStageSpan()


NO_STAGE_TRACING: StageTracer = _NoStageTracing()
NO_SIMULATION_TRACING: SimulationTracer = _NoSimulationTracing()
