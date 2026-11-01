"""Сериализация `ReviewJob` в формат сообщения из `SYSTEM_DESIGN.md` §4.2.

Чистые функции, без брокера: тест не помечен `integration`.
"""

import json
from uuid import UUID

from app.domain.entities import ReviewJob, SimulationJob
from app.infrastructure.queue.rabbitmq import (
    from_wire_message,
    job_type,
    simulation_from_wire_message,
    to_wire_message,
)

JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    event_type="pull_request",
    action="opened",
    installation_id=512804923,
    repository_provider_id="987654",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="abc123def",
    base_sha="fed654cba",
)


def test_wire_message_matches_system_design_schema() -> None:
    assert to_wire_message(JOB) == {
        "job_id": str(JOB.id),
        "review_run_id": str(JOB.review_run_id),
        "event_type": "pull_request",
        "action": "opened",
        "installation_id": 512804923,
        "repository": {"id": "987654", "full_name": "owner/repo"},
        "pull_request": {
            "number": 42,
            "head_sha": "abc123def",
            "base_sha": "fed654cba",
        },
    }


def test_from_wire_message_round_trips_to_wire_message() -> None:
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")
    assert from_wire_message(body) == JOB


SIMULATION_JOB = SimulationJob(id=UUID(int=3), simulation_id=UUID(int=4))


def test_a_simulation_message_carries_only_the_ids() -> None:
    """Вход пользователя лежит в строке симуляции: ни кода, ни правил в теле."""
    assert to_wire_message(SIMULATION_JOB) == {
        "job_id": str(SIMULATION_JOB.id),
        "simulation_id": str(SIMULATION_JOB.simulation_id),
    }
    body = json.dumps(to_wire_message(SIMULATION_JOB)).encode("utf-8")
    assert simulation_from_wire_message(body) == SIMULATION_JOB


def test_the_job_type_names_the_kind_of_task() -> None:
    assert (job_type(JOB), job_type(SIMULATION_JOB)) == ("review", "simulation")
