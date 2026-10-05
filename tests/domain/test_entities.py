import dataclasses
from uuid import UUID

import pytest

from app.domain.entities import ReviewJob


def _job(**overrides: object) -> ReviewJob:
    fields = {
        "id": UUID(int=1),
        "review_run_id": UUID(int=2),
        "event_type": "pull_request",
        "action": "opened",
        "installation_id": 512804923,
        "repository_provider_id": "987654",
        "repository_full_name": "owner/repo",
        "pull_request_number": 42,
        "head_sha": "abc123def",
        "base_sha": "fed654cba",
    }
    fields.update(overrides)
    return ReviewJob(**fields)  # type: ignore[arg-type]


def test_review_jobs_with_same_fields_are_equal() -> None:
    assert _job() == _job()


def test_review_jobs_with_different_head_sha_are_not_equal() -> None:
    assert _job() != _job(head_sha="other")


def test_review_job_is_frozen() -> None:
    job = _job()
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.head_sha = "mutated"  # type: ignore[misc]
