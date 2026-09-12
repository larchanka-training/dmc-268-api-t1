"""Literal fixtures. No database, no clock, no network."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.entities import DiffAnchor, Finding, Hunk, ReviewRun
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    ReviewRunStatus,
    TriggerSource,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


def uid(n: int) -> UUID:
    return UUID(int=n)


@pytest.fixture
def run() -> ReviewRun:
    return ReviewRun(
        id=uid(1),
        merge_request_id=uid(2),
        head_sha="abc123",
        status=ReviewRunStatus.QUEUED,
        trigger=TriggerSource.WEBHOOK,
        last_progress_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def make_finding(
    *,
    n: int = 1,
    path: str = "app/main.py",
    side: DiffSide = DiffSide.NEW,
    old_line: int | None = None,
    new_line: int | None = 10,
    category: FindingCategory = FindingCategory.SECURITY,
) -> Finding:
    return Finding(
        id=uid(n),
        review_run_id=uid(2),
        anchor=DiffAnchor(
            file_path=path, side=side, old_line=old_line, new_line=new_line
        ),
        category=category,
        severity=FindingSeverity.HIGH,
        message="message",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture
def hunks() -> list[Hunk]:
    return [
        Hunk(
            file_path="app/main.py",
            new_start=8,
            new_count=5,
            changed_new_lines=frozenset({10, 11, 12}),
            changed_old_lines=frozenset({7}),
        )
    ]
