"""Сценарий модели из файла: конверт записей и модели провода внутри него."""

import json
from pathlib import Path
from typing import Any

import pytest

from app.application.llm_schema import ReviewOutput, VerificationOutput
from app.domain.llm_errors import (
    LlmError,
    LlmOutputError,
    LlmRateLimitedError,
    LlmTimeoutError,
)

from .llm_gateway import SUMMARY, ScriptError, load_llm_script

EXAMPLE = Path(__file__).parent.parent / "sandbox" / "examples" / "llm-script.json"
FINDING = {"filePath": "a.py", "line": 2, "message": "м"}


def _load(tmp_path: Path, entries: Any) -> list[Any]:
    path = tmp_path / "script.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return load_llm_script(path).answers


def _content(tmp_path: Path, entry: dict[str, Any]) -> Any:
    (answer,) = _load(tmp_path, [entry])
    assert not isinstance(answer, Exception)
    return answer.content


def test_the_example_script_drops_f1_and_keeps_f2() -> None:
    review, verify = load_llm_script(EXAMPLE).answers

    assert not isinstance(review, Exception) and not isinstance(verify, Exception)
    assert isinstance(review.content, ReviewOutput)
    assert [(f.line, f.category.value) for f in review.content.findings] == [
        (3, "readability"),
        (7, "correctness"),
    ]
    assert (review.model, review.tokens_used) == ("script", 1200)
    assert isinstance(verify.content, VerificationOutput)
    assert [(v.id, v.verdict.value) for v in verify.content.verdicts] == [
        ("F1", "drop"),
        ("F2", "keep"),
    ]


def test_missing_finding_and_verdict_fields_take_defaults(tmp_path: Path) -> None:
    review = _content(tmp_path, {"review": [FINDING]})
    verify = _content(tmp_path, {"verify": [{"id": "F1", "verdict": "keep"}]})

    (finding,) = review.findings
    assert (finding.side.value, finding.category.value, finding.severity.value) == (
        "new",
        "readability",
        "low",
    )
    assert (finding.confidence, finding.suggestion, finding.rule) == (None, None, None)
    assert review.summary.model_dump(mode="json") == SUMMARY
    assert verify.verdicts[0].reason == "сценарий"


def test_wire_fields_pass_through_to_the_content(tmp_path: Path) -> None:
    finding = FINDING | {
        "confidence": 0.7,
        "suggestion": {"diff": "-a\n+b"},
        "rule": {"name": "имена", "check": 1},
    }
    summary = {"problem": "Плохо.", "positives": ["тесты"], "effort": "день"}

    content = _content(tmp_path, {"review": [finding], "summary": summary})

    (out,) = content.findings
    assert (out.confidence, out.suggestion.diff, out.rule.name, out.rule.check) == (
        0.7,
        "-a\n+b",
        "имена",
        1,
    )
    assert content.summary.positives == ("тесты",)


def test_a_whole_pasted_model_answer_is_accepted(tmp_path: Path) -> None:
    finding = FINDING | {"side": "old", "category": "security", "severity": "high", "confidence": None}
    review = {"schemaVersion": "1", "findings": [finding], "summary": SUMMARY}
    verify = {"schemaVersion": "1", "verdicts": [{"id": "F1", "verdict": "drop", "reason": "р"}]}

    assert _content(tmp_path, {"review": review}) == ReviewOutput.model_validate(review)
    assert _content(tmp_path, {"verify": verify}) == VerificationOutput.model_validate(verify)


@pytest.mark.parametrize(
    ("kind", "error_type"),
    [
        ("output", LlmOutputError),
        ("timeout", LlmTimeoutError),
        ("rate_limited", LlmRateLimitedError),
        ("unavailable", LlmError),
    ],
)
def test_an_error_entry_is_the_matching_llm_error(
    tmp_path: Path, kind: str, error_type: type[LlmError]
) -> None:
    (answer,) = _load(tmp_path, [{"error": kind, "tokens": 5}])

    assert type(answer) is error_type
    assert isinstance(answer, LlmError) and answer.tokens_used == 5


@pytest.mark.parametrize(
    ("entries", "fragment"),
    [
        ({"review": []}, "ждали JSON-список"),
        (["review"], "запись 0: ждали объект"),
        ([{"review": [], "error": "timeout"}], "запись 0: нужен ровно один"),
        ([{"verify": [], "summary": SUMMARY}], "запись 0: лишние ключи"),
        ([{"verify": []}, {"error": "boom"}], "запись 1: error - один из"),
        ([{"error": "timeout", "tokens": True}], "tokens"),
        ([{"review": [FINDING | {"path": "a.py"}]}], r"запись 0: review(.|\n)*findings\.0\.path"),
        ([{"review": [FINDING | {"severity": "x"}]}], r"findings\.0\.severity"),
        ([{"review": [FINDING | {"line": True}]}], r"findings\.0\.line"),
        ([{"review": {"findings": []}}], "schemaVersion"),
        ([{"review": {"schemaVersion": "1"}, "summary": SUMMARY}], "summary - внутри review"),
        ([{"verify": [["F1", "keep"]]}], r"запись 0: verify(.|\n)*verdicts\.0"),
        ([{"verify": [{"id": "F1", "verdict": "maybe"}]}], r"verdicts\.0\.verdict"),
    ],
)
def test_an_invalid_script_is_a_script_error(tmp_path: Path, entries: Any, fragment: str) -> None:
    with pytest.raises(ScriptError, match=fragment):
        _load(tmp_path, entries)


def test_a_non_json_script_is_a_script_error(tmp_path: Path) -> None:
    path = tmp_path / "script.json"
    path.write_text("[", encoding="utf-8")

    with pytest.raises(ScriptError, match="не JSON"):
        load_llm_script(path)
