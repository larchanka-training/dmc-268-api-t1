from datetime import UTC, datetime
from uuid import UUID

from app.domain.entities import DiffAnchor, Finding
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    ThreadFollowUpStatus,
)
from app.domain.review_comment import format_inline_finding_comment, format_thread_reply


def test_inline_comment_has_severity_header() -> None:
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    finding = Finding(
        id=UUID(int=1),
        review_run_id=UUID(int=2),
        anchor=DiffAnchor(file_path="a.py", side=DiffSide.NEW, new_line=1),
        category=FindingCategory.CORRECTNESS,
        severity=FindingSeverity.HIGH,
        message="описание",
        created_at=moment,
        updated_at=moment,
    )
    text = format_inline_finding_comment(finding)
    assert "🟠 Существенно" in text
    assert "Корректность" in text
    assert "описание" in text


def test_accepted_risk_on_critical_adds_manual_resolve_hint() -> None:
    text = format_thread_reply(
        ThreadFollowUpStatus.ACCEPTED_RISK,
        "риск приемлем",
        severity=FindingSeverity.CRITICAL,
    )
    assert "🤝" in text
    assert "вручную" in text
