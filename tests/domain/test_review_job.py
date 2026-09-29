"""Сущность `ReviewJob`: замороженная запись сообщения очереди.

Набор полей сверен с `tasks/plan.md` (Task 3.1) и `docs/SYSTEM_DESIGN.md`
§4.2: список ниже фиксирует и отклонение от плана — `action` и
`repository_provider_id`, без которых тело сообщения §4.2 не собрать.
"""

from dataclasses import FrozenInstanceError, fields
from uuid import UUID

import pytest

from app.domain.entities import ReviewJob

JOB = ReviewJob(
    job_id=UUID("0192f0c0-0000-7000-8000-000000000001"),
    review_run_id=UUID("0192f0c0-0000-7000-8000-000000000002"),
    repository_full_name="larchanka-training/dmc-268-api-t1",
    repository_provider_id=923478362,
    pr_number=6,
    head_sha="a1b2c3d4e5f6789012345678abcdef0123456789",
    base_sha="fed654cba0fed654cba0fed654cba0fed654cba0",
    action="opened",
    priority=5,
)


def test_field_set_matches_plan_and_section_4_2() -> None:
    """Поля плана плюс `action` и `repository_provider_id` из §4.2 — и ничего сверх."""
    names = [field.name for field in fields(ReviewJob)]
    assert names == [
        "job_id",
        "review_run_id",
        "repository_full_name",
        "repository_provider_id",
        "pr_number",
        "head_sha",
        "base_sha",
        "action",
        "priority",
    ]


def test_job_is_frozen() -> None:
    with pytest.raises(FrozenInstanceError):
        JOB.priority = 9  # type: ignore[misc]


def test_job_has_no_dict_behind_slots() -> None:
    assert not hasattr(JOB, "__dict__")


def test_base_sha_is_optional() -> None:
    job = ReviewJob(
        job_id=JOB.job_id,
        review_run_id=JOB.review_run_id,
        repository_full_name=JOB.repository_full_name,
        repository_provider_id=JOB.repository_provider_id,
        pr_number=JOB.pr_number,
        head_sha=JOB.head_sha,
        base_sha=None,
        action="synchronize",
        priority=0,
    )
    assert job.base_sha is None
