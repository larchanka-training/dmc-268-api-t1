"""Находки без треда в базе - для merge gate открыты (#185)."""

from datetime import UTC, datetime
from uuid import UUID

from app.domain.entities import DiffAnchor, Finding, PublishedComment
from app.domain.enums import CommentKind, DiffSide, FindingCategory, FindingSeverity
from app.domain.merge_gate import untracked_findings

NOW = datetime(2026, 10, 25, tzinfo=UTC)
RUN_ID = UUID(int=1)


def _finding(n: int, *, covered_by: UUID | None = None) -> Finding:
    return Finding(
        id=UUID(int=n),
        review_run_id=RUN_ID,
        anchor=DiffAnchor(file_path="a.py", side=DiffSide.NEW, new_line=n),
        category=FindingCategory.SECURITY,
        severity=FindingSeverity.CRITICAL,
        message="m",
        created_at=NOW,
        updated_at=NOW,
        covered_by_finding_id=covered_by,
    )


def _comment(n: int, kind: CommentKind, finding_id: UUID | None = None) -> PublishedComment:
    return PublishedComment(
        id=UUID(int=100 + n),
        review_run_id=RUN_ID,
        finding_id=finding_id,
        provider_comment_id=str(n),
        kind=kind,
        published_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )


def test_finding_without_its_inline_record_in_a_published_review_is_untracked() -> None:
    tracked, lost, covered = _finding(10), _finding(11), _finding(12, covered_by=UUID(int=99))
    comments = [_comment(1, CommentKind.SUMMARY), _comment(2, CommentKind.INLINE, tracked.id)]

    assert untracked_findings([tracked, lost, covered], comments) == (lost,)


def test_run_without_a_published_review_has_no_untracked_findings() -> None:
    assert untracked_findings([_finding(10)], []) == ()
