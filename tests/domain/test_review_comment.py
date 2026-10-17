from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.entities import DiffAnchor, Finding, RuleRef
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


_MOMENT = datetime(2026, 10, 16, tzinfo=UTC)
_PLAIN = Finding(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    anchor=DiffAnchor(file_path="a.py", side=DiffSide.NEW, new_line=1),
    category=FindingCategory.CORRECTNESS,
    severity=FindingSeverity.HIGH,
    message="Ошибка глотается.",
    created_at=_MOMENT,
    updated_at=_MOMENT,
)


def test_finding_by_owner_rule_starts_its_text_with_the_attribution_prefix() -> None:
    finding = replace(
        _PLAIN, rule=RuleRef("Error Handling Standards", 2, "Текст ошибки воспроизводим")
    )
    assert format_inline_finding_comment(finding) == (
        "⚠️ Потенциальная проблема | 🟠 Существенно | Корректность\n"
        "\n"
        "Согласно пользовательскому правилу «Error Handling Standards» "
        "(пункт 2: Текст ошибки воспроизводим): Ошибка глотается."
    )


def test_standard_finding_carries_no_prefix() -> None:
    assert format_inline_finding_comment(_PLAIN) == (
        "⚠️ Потенциальная проблема | 🟠 Существенно | Корректность\n\nОшибка глотается."
    )
