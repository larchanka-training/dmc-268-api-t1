"""Заготовка сборки контекста — чистая функция, без БД и сети."""

from datetime import UTC, datetime
from uuid import UUID

from app.application.context_assembly import (
    PLACEHOLDER_FILE_PATH,
    assemble_context_stub,
    stub_hunks,
)
from app.domain.entities import ReviewJob

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)

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


def _new_id() -> UUID:
    return UUID(int=99)


def test_result_carries_the_diff_tier_and_the_given_review_run() -> None:
    payload = assemble_context_stub(JOB, review_run_id=UUID(int=2), now=NOW, new_id=_new_id)
    assert payload.id == UUID(int=99)
    assert payload.review_run_id == UUID(int=2)
    assert payload.chunk_index == 0
    assert payload.tiers == ("diff",)
    assert payload.created_at == NOW
    assert payload.updated_at == NOW


def test_result_is_deterministic_for_the_same_inputs() -> None:
    first = assemble_context_stub(JOB, review_run_id=UUID(int=2), now=NOW, new_id=_new_id)
    second = assemble_context_stub(JOB, review_run_id=UUID(int=2), now=NOW, new_id=_new_id)
    assert first.content_sha256 == second.content_sha256
    assert first.body == second.body


def test_stub_hunks_cover_the_placeholder_file() -> None:
    hunks = stub_hunks()
    assert len(hunks) == 1
    assert hunks[0].file_path == PLACEHOLDER_FILE_PATH
    assert hunks[0].changed_new_lines
