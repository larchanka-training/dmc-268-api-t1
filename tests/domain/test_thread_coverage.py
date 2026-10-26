"""Покрыта ли находка повторного прогона тредом бота (#148, #169 п. 3а)."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.entities import BotThread, DiffAnchor, Finding
from app.domain.enums import DiffSide, FindingCategory, FindingSeverity
from app.domain.thread_coverage import thread_coverage

NOW = datetime(2026, 10, 25, tzinfo=UTC)
LOW, HIGH, CRITICAL = FindingSeverity.LOW, FindingSeverity.HIGH, FindingSeverity.CRITICAL


def _finding(
    severity: FindingSeverity,
    *,
    line: int = 10,
    side: DiffSide = DiffSide.NEW,
    category: FindingCategory = FindingCategory.CORRECTNESS,
    path: str = "app/x.py",
) -> Finding:
    anchor = (
        DiffAnchor(path, side, new_line=line)
        if side is DiffSide.NEW
        else DiffAnchor(path, side, old_line=line)
    )
    return Finding(
        id=UUID(int=line),
        review_run_id=UUID(int=1),
        anchor=anchor,
        category=category,
        severity=severity,
        message="m",
        created_at=NOW,
        updated_at=NOW,
    )


def _thread(
    severity: FindingSeverity, *, resolved: bool = False, by_bot: bool = False, **where: object
) -> BotThread:
    return BotThread(
        finding=_finding(severity, **where),  # type: ignore[arg-type]
        root_provider_comment_id="root",
        root_published_comment_id=UUID(int=99),
        resolved=resolved,
        resolved_by_bot=by_bot,
    )


@pytest.mark.parametrize(
    ("thread", "finding", "covered"),
    [
        (_thread(HIGH), _finding(HIGH), True),
        (_thread(HIGH), _finding(LOW), True),
        (_thread(HIGH), _finding(CRITICAL), False),
        (_thread(LOW), _finding(HIGH), False),
        (_thread(CRITICAL, resolved=True), _finding(CRITICAL), True),
        (_thread(LOW, resolved=True), _finding(HIGH), True),
        (_thread(HIGH, resolved=True), _finding(CRITICAL), False),
        (_thread(CRITICAL, resolved=True, by_bot=True), _finding(LOW), False),
        (_thread(HIGH, category=FindingCategory.SECURITY), _finding(HIGH), False),
        (_thread(HIGH, path="app/y.py"), _finding(HIGH), False),
        (_thread(HIGH, side=DiffSide.OLD), _finding(HIGH, side=DiffSide.OLD), True),
        (_thread(HIGH, side=DiffSide.OLD), _finding(HIGH), False),
    ],
    ids=[
        "open-same",
        "open-lower",
        "open-escalated",
        "open-escalated-non-critical",
        "human-same",
        "human-escalated-non-critical",
        "human-escalated-critical",
        "bot-resolved",
        "other-category",
        "other-file",
        "old-side",
        "other-side",
    ],
)
def test_coverage_on_the_threads_current_line(
    thread: BotThread, finding: Finding, covered: bool
) -> None:
    coverage = thread_coverage(finding, (thread,), {"root": 10})

    assert (coverage.covered_by is thread) is covered


@pytest.mark.parametrize("current_line", [None, 12], ids=["outdated", "moved-away"])
def test_thread_elsewhere_now_covers_nothing(current_line: int | None) -> None:
    thread = _thread(CRITICAL)

    coverage = thread_coverage(_finding(CRITICAL), (thread,), {"root": current_line})

    assert coverage.covered_by is None


def test_thread_unknown_to_the_host_covers_nothing() -> None:
    assert thread_coverage(_finding(HIGH), (_thread(HIGH),), {}).covered_by is None


def test_escalated_finding_is_not_covered_by_the_open_thread() -> None:
    coverage = thread_coverage(_finding(CRITICAL), (_thread(HIGH),), {"root": 10})

    assert coverage.covered_by is None
