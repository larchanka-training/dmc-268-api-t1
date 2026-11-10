"""Merge gate: порог важности и находки без треда в базе (#185)."""

from datetime import UTC, datetime
from uuid import UUID

from app.domain.entities import DiffAnchor, Finding, PublishedComment
from app.domain.enums import CommentKind, DiffSide, FindingCategory, FindingSeverity
from app.domain.merge_gate import merge_blocked_by_findings, untracked_findings

NOW = datetime(2026, 10, 25, tzinfo=UTC)
RUN_ID = UUID(int=1)


def _finding(
    n: int, *, covered_by: UUID | None = None, severity: FindingSeverity = FindingSeverity.CRITICAL
) -> Finding:
    return Finding(
        id=UUID(int=n),
        review_run_id=RUN_ID,
        anchor=DiffAnchor(file_path="a.py", side=DiffSide.NEW, new_line=n),
        category=FindingCategory.SECURITY,
        severity=severity,
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


_LOW, _MEDIUM, _HIGH, _CRITICAL = (
    FindingSeverity.LOW,
    FindingSeverity.MEDIUM,
    FindingSeverity.HIGH,
    FindingSeverity.CRITICAL,
)


def test_open_finding_at_threshold_blocks_merge() -> None:
    assert merge_blocked_by_findings((_finding(1, severity=_HIGH),), _HIGH) is True


def test_open_finding_below_threshold_does_not_block_merge() -> None:
    assert merge_blocked_by_findings((_finding(1, severity=_HIGH),), _CRITICAL) is False


def test_open_critical_finding_blocks_merge_at_default_threshold() -> None:
    assert merge_blocked_by_findings((_finding(1, severity=_CRITICAL),), _CRITICAL) is True


def test_no_open_findings_do_not_block_merge() -> None:
    assert merge_blocked_by_findings((), _LOW) is False
