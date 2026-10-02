"""Сериализация `ReviewJob` в формат сообщения из `SYSTEM_DESIGN.md` §4.2.

Чистые функции, без брокера: тест не помечен `integration`.
"""

import json
from uuid import UUID

from app.domain.entities import ReviewJob
from app.infrastructure.queue.rabbitmq import from_wire_message, to_wire_message

JOB = ReviewJob(
    id=UUID(int=1),
    event_type="pull_request",
    action="opened",
    repository_provider_id="987654",
    repository_full_name="owner/repo",
    pull_request_number=42,
    head_sha="abc123def",
    base_sha="fed654cba",
)


def test_wire_message_matches_system_design_schema() -> None:
    assert to_wire_message(JOB) == {
        "job_id": str(JOB.id),
        "event_type": "pull_request",
        "action": "opened",
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
