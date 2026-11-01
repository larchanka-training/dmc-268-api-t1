"""Оркестрация одной симуляции песочницы: queued → ... → completed | failed.

Анализ - те же функции, что у ревью запроса на изменение (design D5): сборка
пакета, `request_review`, детерминированный отсев, `verify_findings`. Без VCS,
без шагов прогона и без публикации; ход - статусом симуляции, каждый переход -
свой коммит, чтобы проход обслуживания видел прогресс.

Сбой - статус, а не исключение (D7): ошибка модели, разбора её ответа или
программы переводит симуляцию в `failed` с кодом и потраченными токенами, и
воркер подтверждает сообщение. Сбой верификатора симуляцию не роняет: итог
`unavailable`, замечания - после отсева. Симуляция, которую закрыли без
воркера (зависшая) или удалили по сроку, дальше не обрабатывается.

В лог и спаны не идут ни код, ни правила, ни замечания (D9): только статусы,
коды и счётчики; идентификатор симуляции - атрибутом из контекста логов.
"""

import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID

from app.application.context_assembly import ContextLimits
from app.application.llm_review import LlmLimits, request_review
from app.application.ports import UnitOfWork
from app.application.ports.definition_extractor import DefinitionExtractor
from app.application.ports.llm_gateway import LlmGateway
from app.application.ports.stage_tracer import NO_SIMULATION_TRACING, SimulationTracer
from app.application.use_cases.build_simulation_context import build_simulation_context
from app.application.verify_findings import verify_findings
from app.domain.entities import Simulation, SimulationJob
from app.domain.enums import SimulationStatus
from app.domain.llm_errors import LlmError
from app.domain.prompt import PromptTexts
from app.domain.run_steps import error_code
from app.domain.simulation import (
    SIMULATION_STALE,
    SIMULATION_TERMINAL_STATUSES,
    advance_simulation,
    complete_simulation,
    fail_simulation,
    simulation_result,
)
from app.domain.verification import screen_findings

logger = logging.getLogger(__name__)

S = SimulationStatus


class _ClosedElsewhere(Exception):
    """Симуляцию закрыли без воркера (зависшая) - работа дальше не нужна."""


def run_simulation(
    job: SimulationJob,
    *,
    uow: UnitOfWork,
    llm_gateway: LlmGateway,
    definition_extractor: DefinitionExtractor,
    context_limits: ContextLimits,
    llm_limits: LlmLimits,
    prompt_texts: PromptTexts,
    now: Callable[[], datetime],
    new_id: Callable[[], UUID],
    tracer: SimulationTracer = NO_SIMULATION_TRACING,
) -> None:
    """Довести симуляцию до итога; исключение анализа не выходит наружу.

    Повторная доставка итоговой и удалённой по сроку симуляции - без работы.
    Доставка симуляции, застрявшей посреди анализа (прошлый воркер умер), -
    `failed` с `SIMULATION_STALE`: переходы назад не идут, а вход пользователя
    стирается с итогом; пользователь запустит новую.
    """
    with uow:
        simulation = uow.simulations.get(job.simulation_id)
    if simulation is None:
        logger.info("симуляции нет (удалена по сроку), задача подтверждена без работы")
        return
    if simulation.status in SIMULATION_TERMINAL_STATUSES:
        logger.info("симуляция уже %s, повторная доставка подтверждена без работы", simulation.status)
        return
    if simulation.status is not S.QUEUED:
        logger.warning("симуляция застряла в %s после прошлой доставки, закрыта", simulation.status)
        _fail(uow, simulation.id, SIMULATION_STALE, now())
        return

    spent = 0
    model: str | None = None
    try:
        simulation = _advance(uow, simulation, S.BUILDING_CONTEXT, now())
        with _stage(tracer, simulation.id, S.BUILDING_CONTEXT):
            assembled = build_simulation_context(
                simulation,
                extractor=definition_extractor,
                limits=context_limits,
                now=now(),
                new_id=new_id,
            )
        context = assembled.payload

        simulation = _advance(uow, simulation, S.ANALYSING, now())
        with _stage(tracer, simulation.id, S.ANALYSING):
            answer = request_review(context, prompt_texts, llm_gateway, limits=llm_limits)
            spent, model = answer.tokens_used, answer.model
            # Находка требует владельца-прогона; у симуляции владелец - она сама,
            # а в результат идентификатор прогона не попадает.
            screening = screen_findings(
                answer.findings,
                hunks=assembled.hunks,
                shown_rules=assembled.rules,
                review_run_id=simulation.id,
                now=now(),
                new_id=new_id,
            )

        simulation = _advance(uow, simulation, S.VERIFYING, now())
        with _stage(tracer, simulation.id, S.VERIFYING):
            verification = verify_findings(
                context, prompt_texts, llm_gateway, llm_limits, screening.kept
            )
        spent += verification.tokens_used

        result = simulation_result(
            rejected=screening.rejected,
            kept=verification.kept,
            verdicts=verification.applied,
            context_body=context.body,
        )
        done = complete_simulation(
            simulation,
            result,
            model=answer.model,
            tokens_used=spent,
            verification=verification.outcome,
            now=now(),
        ).unwrap()
        _write(uow, done)
        logger.info(
            "симуляция завершена: замечаний %d, отброшено %d, верификация %s, токенов %d",
            len(result.findings),
            len(result.discarded),
            verification.outcome,
            spent,
        )
    except _ClosedElsewhere:
        logger.info("симуляцию закрыли во время анализа, результат отброшен")
    except Exception as exc:
        code = error_code(exc)
        # Токены упавшего вызова модели - сверх уже потраченных.
        if isinstance(exc, LlmError):
            spent += exc.tokens_used
        logger.exception("симуляция упала: %s", code)
        _fail(uow, job.simulation_id, code, now(), model=model, tokens_used=spent or None)


def _advance(uow: UnitOfWork, simulation: Simulation, status: SimulationStatus, now: datetime) -> Simulation:
    moved = advance_simulation(simulation, status, now).unwrap()
    _write(uow, moved)
    return moved


def _write(uow: UnitOfWork, simulation: Simulation) -> None:
    with uow:
        written = uow.simulations.update(simulation)
        uow.commit()
    if not written:
        raise _ClosedElsewhere


def _fail(
    uow: UnitOfWork,
    simulation_id: UUID,
    code: str,
    now: datetime,
    *,
    model: str | None = None,
    tokens_used: int | None = None,
) -> None:
    """Закрыть симуляцию сбоем по свежей строке из базы.

    Копия в памяти могла отстать (воркер упал между переходами). Итоговую и
    удалённую не трогаем. Сбой этой записи логируется и не выходит наружу:
    незавершённую симуляцию закроет проход обслуживания как зависшую.
    """
    try:
        with uow:
            simulation = uow.simulations.get(simulation_id)
            if simulation is None or simulation.status in SIMULATION_TERMINAL_STATUSES:
                return
            failed = fail_simulation(simulation, code, now, model=model, tokens_used=tokens_used).unwrap()
            uow.simulations.update(failed)
            uow.commit()
    except Exception:
        logger.exception("не удалось перевести симуляцию в failed")


@contextmanager
def _stage(tracer: SimulationTracer, simulation_id: UUID, stage: SimulationStatus) -> Iterator[None]:
    """Спан этапа: телеметрия, на исход симуляции не влияет."""
    span = tracer.start(simulation_id, stage)
    try:
        yield
    except BaseException as exc:
        span.end(error_code(exc))
        raise
    span.end(None)
