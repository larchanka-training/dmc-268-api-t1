"""Сериализация `ReviewJob` в формат сообщения из `SYSTEM_DESIGN.md` §4.2.

Чистая функция, без брокера: тест не помечен `integration`.
"""

from uuid import UUID

from app.domain.entities import ReviewJob
from app.infrastructure.queue.rabbitmq import to_wire_message

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
