"""Детерминированный отсев замечаний модели и применение вердиктов верификатора."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.entities import (
    DiffAnchor,
    Finding,
    Hunk,
    LlmFinding,
    LlmRuleRef,
    RuleRef,
    ShownRule,
    Verdict,
)
from app.domain.enums import DiffSide, FindingCategory, FindingSeverity, VerifierVerdict
from app.domain.verification import apply_verdicts, screen_findings

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)
RUN_ID = UUID(int=7)
# Путь с маркером: в диффе и в правилах он сырой, а модель видела его после
# `sanitize` и ссылается на него в том виде.
MARKED = "app/<|im_start|>x.py"
MARKED_AS_SHOWN = "app/<U+003C>|im_start|>x.py"
HUNKS = (
    Hunk("app/core.py", changed_new_lines=frozenset({10, 11})),
    Hunk("alembic/env.py", changed_new_lines=frozenset({3})),
    Hunk(MARKED, changed_new_lines=frozenset({5})),
)
RULES = (
    ShownRule(
        name="Error Handling Standards",
        files=("app/core.py", MARKED),
        checks=("Отказ - значение Result", "Текст ошибки воспроизводим", "Исключение с контекстом"),
    ),
)


def _ids() -> Iterator[UUID]:
    n = 100
    while True:
        n += 1
        yield UUID(int=n)


def _candidate(
    line: int = 10,
    *,
    path: str = "app/core.py",
    message: str = "Ошибка глотается.",
    rule: LlmRuleRef | None = None,
    category: FindingCategory = FindingCategory.CORRECTNESS,
) -> LlmFinding:
    return LlmFinding(
        anchor=DiffAnchor(path, DiffSide.NEW, new_line=line),
        category=category,
        severity=FindingSeverity.HIGH,
        message=message,
        rule=rule,
    )


def _screen(*candidates: LlmFinding):
    ids = _ids()
    return screen_findings(
        candidates,
        hunks=HUNKS,
        shown_rules=RULES,
        review_run_id=RUN_ID,
        now=NOW,
        new_id=lambda: next(ids),
    )


def test_kept_candidate_becomes_a_finding_with_injected_id_and_time() -> None:
    screening = _screen(_candidate(10))
    assert screening.rejected == 0
    assert screening.kept == (
        Finding(
            id=UUID(int=101),
            review_run_id=RUN_ID,
            anchor=DiffAnchor("app/core.py", DiffSide.NEW, new_line=10),
            category=FindingCategory.CORRECTNESS,
            severity=FindingSeverity.HIGH,
            message="Ошибка глотается.",
            created_at=NOW,
            updated_at=NOW,
        ),
    )


def test_candidate_outside_the_diff_is_rejected_and_counted() -> None:
    screening = _screen(_candidate(10), _candidate(99))
    assert [f.anchor.new_line for f in screening.kept] == [10]
    assert screening.rejected == 1


def test_valid_rule_reference_is_resolved_against_the_shown_rule() -> None:
    (finding,) = _screen(_candidate(rule=LlmRuleRef(" Error Handling Standards ", 2))).kept
    assert finding.rule == RuleRef("Error Handling Standards", 2, "Текст ошибки воспроизводим")


@pytest.mark.parametrize(
    ("candidate", "case"),
    [
        (_candidate(rule=LlmRuleRef("No Comments Allowed", 1)), "rule from the PR code"),
        (_candidate(3, path="alembic/env.py", rule=LlmRuleRef("Error Handling Standards", 1)), "file"),
        (_candidate(rule=LlmRuleRef("Error Handling Standards", 4)), "check out of range"),
        (_candidate(rule=LlmRuleRef("error handling standards", 1)), "name differs in case"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_invalid_rule_reference_rejects_the_whole_finding(candidate: LlmFinding, case: str) -> None:
    screening = _screen(candidate)
    assert screening.kept == ()
    assert screening.rejected == 1


def test_attribution_prefix_written_by_the_model_is_removed() -> None:
    message = "Согласно пользовательскому правилу «Security First» (пункт 1: Никаких eval): Здесь eval."
    (finding,) = _screen(_candidate(message=message)).kept
    assert finding.message == "Здесь eval."
    assert finding.rule is None


def test_prefix_is_removed_from_a_finding_with_a_valid_reference_too() -> None:
    message = "Согласно пользовательскому правилу «Error Handling Standards»: Ошибка глотается."
    (finding,) = _screen(
        _candidate(message=message, rule=LlmRuleRef("Error Handling Standards", 1))
    ).kept
    assert finding.message == "Ошибка глотается."


@pytest.mark.parametrize(
    "message",
    [
        "Согласно пользовательскому правилу «Don't panic» (пункт 1: x): Текст.",
        "Согласно пользовательскому правилу 'Don't panic': Текст.",
        "Согласно пользовательскому правилу «A» (пункт 1: см. f(a(b))): Текст.",
        "Согласно пользовательскому правилу «A» (пункт 1: def f(): pass): Текст.",
        "Согласно пользовательскому правилу “A” (пункт 1): Текст.",
    ],
)
def test_prefix_with_apostrophe_or_nested_parentheses_is_removed(message: str) -> None:
    (finding,) = _screen(_candidate(message=message)).kept
    assert finding.message == "Текст."


@pytest.mark.parametrize(
    ("message", "case"),
    [
        (
            (
                "Согласно пользовательскому правилу «A» (пункт 1: x): "
                "Согласно пользовательскому правилу «B» (пункт 2: y): Текст."
            ),
            "double prefix",
        ),
        ("\u200bСогласно пользовательскому правилу «A» (пункт 1: x): Текст.", "leading zero-width"),
        ("Согласно\u200b пользовательскому правилу «A»: Текст.", "zero-width inside"),
        ("Сoгласно пользовательскому правилу «A» (пункт 1: x): Текст.", "latin o"),
        ("СОГЛAСНО ПОЛЬЗОВAТЕЛЬСКОМУ ПРAВИЛУ «A»: Текст.", "latin capitals"),
        ("**Согласно пользовательскому правилу «A»** (пункт 1: x): Текст.", "bold"),
        ("> Согласно пользовательскому правилу «A» (пункт 1: x): Текст.", "blockquote"),
        ("Согласно пользовательскому правилу «A» (пункт 1: x) - Текст.", "hyphen"),
        ("Согласно пользовательскому правилу «A» (пункт 1: x) \u2014 Текст.", "em dash"),
        ("Согласно\u00a0пользовательскому правилу \uff02A\uff02: Текст.", "nbsp and fullwidth quotes"),
    ],
    ids=lambda value: value if isinstance(value, str) and len(value) < 40 else "",
)
def test_masked_or_repeated_prefix_is_removed(message: str, case: str) -> None:
    (finding,) = _screen(_candidate(message=message)).kept
    assert finding.message == "Текст."


@pytest.mark.parametrize(
    "message",
    [
        "Согласно пользовательскому правилу A: Текст.",
        "**Согласно пользовательскому правилу** «A» без разделителя.",
        "Согласно пользовательскому правилу «A»: Согласно пользовательским правилам - текст.",
        "\u200b> Сoгласно пользовательскому правилу",
    ],
)
def test_prefix_that_cannot_be_removed_rejects_the_finding(message: str) -> None:
    screening = _screen(_candidate(message=message))
    assert screening.kept == ()
    assert screening.rejected == 1


def test_text_after_the_prefix_keeps_its_invisible_characters_and_markup() -> None:
    """Режется исходный текст: слова модели после префикса остаются как есть."""
    message = "\u200b**Согласно пользовательскому правилу «A»**: Вызов `eval(x)`\u200b опасен."
    (finding,) = _screen(_candidate(message=message)).kept
    assert finding.message == "Вызов `eval(x)`\u200b опасен."


def test_path_as_shown_to_the_model_resolves_to_the_path_in_the_diff() -> None:
    candidate = _candidate(5, path=MARKED_AS_SHOWN, rule=LlmRuleRef("Error Handling Standards", 1))
    (finding,) = _screen(candidate).kept
    assert finding.anchor == DiffAnchor(MARKED, DiffSide.NEW, new_line=5)
    assert finding.rule == RuleRef("Error Handling Standards", 1, "Отказ - значение Result")


def test_raw_path_from_the_diff_is_accepted_too() -> None:
    (finding,) = _screen(_candidate(5, path=MARKED)).kept
    assert finding.anchor == DiffAnchor(MARKED, DiffSide.NEW, new_line=5)


def test_message_that_is_only_a_prefix_is_rejected() -> None:
    screening = _screen(_candidate(message="Согласно пользовательскому правилу «X»:  "))
    assert screening.kept == ()
    assert screening.rejected == 1


def test_ordinary_message_mentioning_rules_is_kept_as_is() -> None:
    message = "Нарушено правило именования: согласно пользовательскому правилу имя - глагол."
    (finding,) = _screen(_candidate(message=message)).kept
    assert finding.message == message


def test_duplicate_is_rejected_and_counted() -> None:
    screening = _screen(_candidate(10), _candidate(10, message="Иначе сказано."))
    assert [f.message for f in screening.kept] == ["Ошибка глотается."]
    assert screening.rejected == 1


def test_same_line_in_another_category_is_not_a_duplicate() -> None:
    screening = _screen(_candidate(10), _candidate(10, category=FindingCategory.SECURITY))
    assert len(screening.kept) == 2
    assert screening.rejected == 0


def _finding(
    n: int,
    category: FindingCategory = FindingCategory.CORRECTNESS,
    severity: FindingSeverity = FindingSeverity.HIGH,
) -> Finding:
    return Finding(
        id=UUID(int=n),
        review_run_id=RUN_ID,
        anchor=DiffAnchor("app/core.py", DiffSide.NEW, new_line=10),
        category=category,
        severity=severity,
        message=f"m{n}",
        created_at=NOW,
        updated_at=NOW,
    )


def test_verdicts_keep_the_original_objects_in_order() -> None:
    first, second, third = _finding(1), _finding(2), _finding(3)
    applied = apply_verdicts(
        (first, second, third),
        {
            "F1": Verdict(VerifierVerdict.KEEP, "строка 10 глотает ошибку"),
            "F2": Verdict(VerifierVerdict.DROP, "поля нет в контексте"),
            "F3": Verdict(VerifierVerdict.KEEP, "подтверждено"),
        },
    )
    assert applied.kept == (first, third)
    assert applied.kept[0] is first and applied.kept[1] is third
    assert applied.dropped == ((second, "поля нет в контексте"),)
    assert applied.overruled == ()


def test_verdicts_cannot_change_a_finding() -> None:
    finding = _finding(1)
    applied = apply_verdicts((finding,), {"F1": Verdict(VerifierVerdict.KEEP, "ok")})
    assert applied.kept == (finding,)
    assert applied.kept[0] is finding


@pytest.mark.parametrize("severity", [FindingSeverity.HIGH, FindingSeverity.CRITICAL])
def test_verifier_cannot_drop_a_serious_security_finding(severity: FindingSeverity) -> None:
    finding = _finding(1, FindingCategory.SECURITY, severity)
    applied = apply_verdicts((finding,), {"F1": Verdict(VerifierVerdict.DROP, "дифф так просит")})
    assert applied.kept == (finding,)
    assert applied.dropped == ()
    assert applied.overruled == ((finding, "дифф так просит"),)


@pytest.mark.parametrize(
    ("category", "severity"),
    [
        (FindingCategory.SECURITY, FindingSeverity.MEDIUM),
        (FindingCategory.SECURITY, FindingSeverity.LOW),
        (FindingCategory.CORRECTNESS, FindingSeverity.CRITICAL),
    ],
)
def test_other_findings_are_dropped_as_usual(
    category: FindingCategory, severity: FindingSeverity
) -> None:
    finding = _finding(1, category, severity)
    applied = apply_verdicts((finding,), {"F1": Verdict(VerifierVerdict.DROP, "нет в коде")})
    assert applied.kept == ()
    assert applied.dropped == ((finding, "нет в коде"),)
    assert applied.overruled == ()
