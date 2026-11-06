"""Форма ответов модели: что принимается, что отвергается и во что переводится."""

import copy
from typing import Any

import pytest
from pydantic import ValidationError

from app.application.llm_schema import (
    ReviewOutput,
    VerificationOutput,
    check_verdicts,
    to_review_result,
)
from app.application.ports.llm_gateway import (
    LlmFinding,
    LlmReviewResult,
    LlmReviewSummary,
    LlmRuleRef,
)
from app.domain.entities import DiffAnchor, Verdict
from app.domain.enums import (
    DiffSide,
    FindingCategory,
    FindingSeverity,
    VerifierVerdict,
)
from app.domain.secret_redaction import REDACTED

REVIEW: dict[str, Any] = {
    "schemaVersion": "1",
    "findings": [
        {
            "filePath": "app/core.py",
            "side": "new",
            "line": 12,
            "category": "correctness",
            "severity": "high",
            "message": "Ошибка глотается без контекста.",
            "confidence": 0.8,
            "suggestion": {"diff": "-    pass\n+    raise"},
            "rule": {"name": "Error Handling Standards", "check": 2},
        }
    ],
    "summary": {"problem": "Ошибки теряются.", "positives": ["Тесты есть"], "effort": "час"},
    "threadFollowUps": [
        {
            "anchor": {"filePath": "app/old.py", "side": "old", "line": 3, "category": "security"},
            "status": "fixed",
            "reply": "Исправлено.",
            "resolveThread": True,
        }
    ],
}

VERIFICATION: dict[str, Any] = {
    "schemaVersion": "1",
    "verdicts": [
        {"id": "F1", "verdict": "keep", "reason": "Строка 12 действительно глотает ошибку."},
        {"id": "F2", "verdict": "drop", "reason": "Поле `retries` в контексте не встречается."},
    ],
}


def test_valid_review_translates_into_exact_result() -> None:
    """`threadFollowUps` проходят проверку формы и в результат не попадают (#73)."""
    result = to_review_result(ReviewOutput.model_validate(REVIEW), model="m")

    assert result == LlmReviewResult(
        model="m",
        findings=(
            LlmFinding(
                anchor=DiffAnchor(file_path="app/core.py", side=DiffSide.NEW, new_line=12),
                category=FindingCategory.CORRECTNESS,
                severity=FindingSeverity.HIGH,
                message="Ошибка глотается без контекста.",
                suggestion="-    pass\n+    raise",
                confidence=0.8,
                rule=LlmRuleRef(name="Error Handling Standards", check=2),
            ),
        ),
        summary=LlmReviewSummary(problem="Ошибки теряются.", positives=("Тесты есть",), effort="час"),
    )


def test_optional_fields_may_be_absent() -> None:
    data = copy.deepcopy(REVIEW)
    del data["threadFollowUps"]
    finding = data["findings"][0]
    del finding["suggestion"], finding["rule"]
    finding["confidence"] = None

    result = to_review_result(ReviewOutput.model_validate(data), model="m")

    assert result.findings[0].rule is None
    assert result.findings[0].suggestion is None
    assert result.findings[0].confidence is None


def test_integer_confidence_is_a_number() -> None:
    data = copy.deepcopy(REVIEW)
    data["findings"][0]["confidence"] = 1

    assert ReviewOutput.model_validate(data).findings[0].confidence == 1.0


def _set(data: dict[str, Any], path: tuple[str | int, ...], value: Any) -> None:
    node: Any = data
    for key in path[:-1]:
        node = node[key]
    if value is _DELETE:
        del node[path[-1]]
    else:
        node[path[-1]] = value


_DELETE = object()
_F = ("findings", 0)
_T = ("threadFollowUps", 0)

# Каждое поле обеих форм - хотя бы одним отвергающим случаем; ошибка указывает на это поле.
REVIEW_REJECTIONS: list[tuple[tuple[str | int, ...], Any]] = [
    (("schemaVersion",), "2"),
    (("schemaVersion",), _DELETE),
    (("findings",), _DELETE),
    (("findings",), "нет"),
    (("summary",), _DELETE),
    (("unexpected",), 1),
    ((*_F, "filePath"), ""),
    ((*_F, "filePath"), _DELETE),
    ((*_F, "side"), "both"),
    ((*_F, "line"), 0),
    ((*_F, "line"), "12"),
    ((*_F, "line"), True),
    ((*_F, "category"), "style"),
    ((*_F, "severity"), "blocker"),
    ((*_F, "severity"), _DELETE),
    ((*_F, "message"), ""),
    ((*_F, "confidence"), 1.5),
    ((*_F, "confidence"), -0.1),
    ((*_F, "confidence"), "0.5"),
    ((*_F, "confidence"), _DELETE),
    ((*_F, "suggestion"), "просто текст"),
    ((*_F, "suggestion", "diff"), _DELETE),
    ((*_F, "suggestion", "extra"), 1),
    ((*_F, "rule", "name"), ""),
    ((*_F, "rule", "check"), 0),
    ((*_F, "rule", "check"), "2"),
    ((*_F, "rule", "check"), _DELETE),
    ((*_F, "rule", "extra"), 1),
    ((*_F, "extra"), 1),
    (("summary", "problem"), ""),
    (("summary", "positives"), "всё"),
    (("summary", "effort"), _DELETE),
    (("summary", "extra"), 1),
    ((*_T, "anchor", "filePath"), ""),
    ((*_T, "anchor", "side"), "both"),
    ((*_T, "anchor", "line"), 0),
    ((*_T, "anchor", "category"), "style"),
    ((*_T, "anchor", "extra"), 1),
    ((*_T, "status"), "ignored"),
    ((*_T, "reply"), ""),
    ((*_T, "resolveThread"), "true"),
    ((*_T, "extra"), 1),
]


@pytest.mark.parametrize(("path", "value"), REVIEW_REJECTIONS)
def test_review_rejects_each_malformed_field(path: tuple[str | int, ...], value: Any) -> None:
    data = copy.deepcopy(REVIEW)
    _set(data, path, value)

    with pytest.raises(ValidationError) as caught:
        ReviewOutput.model_validate(data)

    locations = [error["loc"] for error in caught.value.errors()]
    assert any(loc[: len(path)] == path for loc in locations), locations


VERIFICATION_REJECTIONS: list[tuple[tuple[str | int, ...], Any]] = [
    (("schemaVersion",), "2"),
    (("verdicts",), _DELETE),
    (("unexpected",), 1),
    (("verdicts", 0, "id"), ""),
    (("verdicts", 0, "verdict"), "maybe"),
    (("verdicts", 0, "reason"), ""),
    (("verdicts", 0, "extra"), 1),
]


@pytest.mark.parametrize(("path", "value"), VERIFICATION_REJECTIONS)
def test_verification_rejects_each_malformed_field(
    path: tuple[str | int, ...], value: Any
) -> None:
    data = copy.deepcopy(VERIFICATION)
    _set(data, path, value)

    with pytest.raises(ValidationError) as caught:
        VerificationOutput.model_validate(data)

    locations = [error["loc"] for error in caught.value.errors()]
    assert any(loc[: len(path)] == path for loc in locations), locations


def test_verdicts_covering_every_candidate_once_are_accepted() -> None:
    verdicts = check_verdicts(VerificationOutput.model_validate(VERIFICATION), ("F1", "F2"))

    assert verdicts.unwrap() == {
        "F1": Verdict(VerifierVerdict.KEEP, "Строка 12 действительно глотает ошибку."),
        "F2": Verdict(VerifierVerdict.DROP, "Поле `retries` в контексте не встречается."),
    }


@pytest.mark.parametrize(
    ("ids", "fragment"),
    [
        (("F1", "F2", "F3"), "F3"),
        (("F1",), "F2"),
    ],
    ids=["missing", "unknown"],
)
def test_verdicts_must_match_candidates_exactly(ids: tuple[str, ...], fragment: str) -> None:
    result = check_verdicts(VerificationOutput.model_validate(VERIFICATION), ids)

    assert not result.ok
    assert fragment in (result.error or "")


def test_repeated_verdict_is_rejected() -> None:
    data = copy.deepcopy(VERIFICATION)
    data["verdicts"][1]["id"] = "F1"

    result = check_verdicts(VerificationOutput.model_validate(data), ("F1", "F2"))

    assert not result.ok
    assert "F1" in (result.error or "")


TOKEN = "ghp_" + "a1B2" * 9


def test_secret_quoted_by_the_review_is_replaced_before_it_leaves_the_schema() -> None:
    """Секрет из диффа не уходит в `findings`, сводку и комментарии PR (#315)."""
    raw = copy.deepcopy(REVIEW)
    raw["findings"][0]["message"] = f"Зашит токен `{TOKEN}`."
    raw["findings"][0]["suggestion"] = {"diff": f'-TOKEN = "{TOKEN}"\n+TOKEN = os.environ["TOKEN"]'}
    raw["summary"] = {"problem": f"Токен {TOKEN} в коде.", "positives": [f"не {TOKEN}"], "effort": TOKEN}

    result = to_review_result(ReviewOutput.model_validate(raw), model="m")

    summary = result.summary
    assert summary is not None
    suggestion = result.findings[0].suggestion
    assert suggestion is not None
    texts = [result.findings[0].message, suggestion, summary.problem, *summary.positives, summary.effort]
    assert all(TOKEN not in text and REDACTED in text for text in texts)
    # Правка в строке `+` секрета не содержит и остаётся.
    assert '+TOKEN = os.environ["TOKEN"]' in suggestion


def test_suggestion_without_a_secret_is_kept() -> None:
    result = to_review_result(ReviewOutput.model_validate(REVIEW), model="m")
    assert result.findings[0].suggestion == "-    pass\n+    raise"


def test_secret_quoted_by_the_verifier_does_not_reach_the_log() -> None:
    raw = copy.deepcopy(VERIFICATION)
    raw["verdicts"][0]["reason"] = f"Строка с `{TOKEN}` — настоящий токен."

    verdicts = check_verdicts(VerificationOutput.model_validate(raw), ("F1", "F2")).unwrap()

    assert TOKEN not in verdicts["F1"].reason
    assert REDACTED in verdicts["F1"].reason


def test_finding_on_the_old_side_is_anchored_to_the_old_line() -> None:
    """Сторона `old` даёт якорь со старой строкой, а не с новой."""
    data = copy.deepcopy(REVIEW)
    data["findings"][0].update(filePath="app/gone.py", side="old", line=7)

    result = to_review_result(ReviewOutput.model_validate(data), model="m")

    assert result.findings[0].anchor == DiffAnchor(
        file_path="app/gone.py", side=DiffSide.OLD, old_line=7
    )
