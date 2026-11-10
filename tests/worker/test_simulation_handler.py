"""Обработчик задачи симуляции и выбор обработчика по виду задачи. Без БД и брокера."""

import io
import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from app.application.ports.llm_gateway import LlmGateway
from app.domain.entities import SimulationJob
from app.domain.enums import SimulationSourceKind, SimulationStatus, VerificationOutcome
from app.domain.llm_errors import LlmTimeoutError
from app.domain.simulation import new_simulation
from app.infrastructure.llm.stub import StubLlmGateway
from app.infrastructure.logging import JsonFormatter, SimulationIdFilter
from app.infrastructure.queue.rabbitmq import to_wire_message
from app.worker.factory import (
    UnknownJobType,
    Worker,
    build_message_handler,
    build_simulation_handler,
)

from ..fakes.context import FakeExtractor
from ..fakes.llm_gateway import ScriptedLlmGateway
from ..fakes.queue import FakeChannel, FakeConnection
from ..fakes.unit_of_work import FakeUnitOfWork
from .test_review_handler import LIMITS, LLM_LIMITS, TEXTS

JOB = SimulationJob(id=UUID(int=10), simulation_id=UUID(int=11))
BODY = json.dumps(to_wire_message(JOB)).encode("utf-8")
SECRET_CODE = 'TOKEN = "ghp_' + "a1B2" * 9 + '"\n'


class _Deps:
    """Только то, что нужно обработчику симуляции: без VCS и кэша."""

    def __init__(self, uow: FakeUnitOfWork, gateway: LlmGateway | None = None) -> None:
        self._uow = uow
        self._gateway = gateway or StubLlmGateway()

    def unit_of_work(self) -> FakeUnitOfWork:
        return self._uow

    def llm_gateway(self) -> LlmGateway:
        return self._gateway

    def definition_extractor(self) -> FakeExtractor:
        return FakeExtractor()


def _seeded() -> FakeUnitOfWork:
    uow = FakeUnitOfWork()
    uow.simulations.add(
        new_simulation(
            simulation_id=JOB.simulation_id,
            user_id=UUID(int=12),
            source_kind=SimulationSourceKind.CODE,
            source=SECRET_CODE,
            rules="",
            file_path="app/settings.py",
            now=datetime.now(UTC) - timedelta(seconds=5),
            ttl=timedelta(days=1),
        )
    )
    return uow


def test_the_handler_parses_the_message_and_completes_the_simulation() -> None:
    uow = _seeded()

    build_simulation_handler(_Deps(uow), LIMITS, LLM_LIMITS, TEXTS)(BODY)

    simulation = uow.simulations.get(JOB.simulation_id)
    assert simulation is not None
    assert simulation.status is SimulationStatus.COMPLETED
    assert (simulation.model, simulation.verification) == ("stub-llm", VerificationOutcome.NOT_REQUIRED)


def test_a_failed_simulation_logs_its_id_as_an_attribute_and_no_input() -> None:
    """Сбой модели - запись с атрибутом `simulation_id`, без кода пользователя; сообщение не падает."""
    uow = _seeded()
    gateway = ScriptedLlmGateway([LlmTimeoutError("модель молчит")])
    out = io.StringIO()
    record_handler = logging.StreamHandler(out)
    record_handler.setFormatter(JsonFormatter("dmc-268-test"))
    record_handler.addFilter(SimulationIdFilter())
    pipeline_logger = logging.getLogger("app.application.simulation_pipeline")
    pipeline_logger.addHandler(record_handler)
    try:
        build_simulation_handler(_Deps(uow, gateway), LIMITS, LLM_LIMITS, TEXTS)(BODY)
    finally:
        pipeline_logger.removeHandler(record_handler)

    records = [json.loads(line) for line in out.getvalue().splitlines() if line]
    assert records, "сбой симуляции пишется в лог"
    assert {record["simulation_id"] for record in records} == {str(JOB.simulation_id)}
    assert "ghp_" not in out.getvalue()
    simulation = uow.simulations.get(JOB.simulation_id)
    assert simulation is not None and simulation.error_code == "LLM_TIMEOUT"


def test_an_unreadable_simulation_message_goes_to_the_dead_letter_queue() -> None:
    with pytest.raises(json.JSONDecodeError):
        build_simulation_handler(_Deps(FakeUnitOfWork()), LIMITS, LLM_LIMITS, TEXTS)(b"{not json")


@pytest.mark.parametrize(
    ("kind", "expected"),
    [(None, "review"), ("review", "review"), ("simulation", "simulation")],
    ids=["без вида - ревью", "ревью", "симуляция"],
)
def test_the_message_handler_picks_the_handler_by_job_type(kind: str | None, expected: str) -> None:
    called: list[tuple[str, bytes]] = []
    handler = build_message_handler(
        review=lambda body: called.append(("review", body)),
        simulation=lambda body: called.append(("simulation", body)),
    )

    handler(b"{}", kind)

    assert called == [(expected, b"{}")]


@pytest.mark.parametrize("simulation_handler", [True, False], ids=["чужой вид", "без обработчика симуляции"])
def test_an_unknown_job_type_is_refused(simulation_handler: bool) -> None:
    handler = build_message_handler(
        review=lambda body: None,
        simulation=(lambda body: None) if simulation_handler else None,
    )
    kind = "inspection" if simulation_handler else "simulation"

    with pytest.raises(UnknownJobType, match=kind):
        handler(b"{}", kind)


def test_the_worker_passes_the_amqp_type_to_the_handler() -> None:
    seen: list[str | None] = []
    channel = FakeChannel(
        [
            (SimpleNamespace(delivery_tag=1, redelivered=False), SimpleNamespace(headers={}, type="simulation"), b"{}"),
            (SimpleNamespace(delivery_tag=2, redelivered=False), None, b"{}"),
        ]
    )

    Worker(connection=FakeConnection(), channel=channel).run(  # type: ignore[arg-type]
        lambda body, kind: seen.append(kind), limit=2
    )

    assert seen == ["simulation", None]
    assert channel.acked == [1, 2]
