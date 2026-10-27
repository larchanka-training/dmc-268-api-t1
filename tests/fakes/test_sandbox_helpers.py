"""Сценарий модели из файла и сбои `FakeGitHub` для песочницы."""

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

from .github_api import (
    FAULTS,
    CreateReviewFailure,
    FakeGitHub,
    FaultError,
    app_private_key,
)
from .llm_gateway import ScriptError, load_llm_script

EXAMPLE = Path(__file__).parent.parent / "sandbox" / "examples" / "llm-script.json"


def _script(tmp_path: Path, entries: Any) -> Path:
    path = tmp_path / "script.json"
    path.write_text(json.dumps(entries), encoding="utf-8")
    return path


# --- сценарий модели --------------------------------------------------------


def test_the_example_script_is_a_review_then_a_verification() -> None:
    review, verify = load_llm_script(EXAMPLE).answers

    assert not isinstance(review, Exception) and not isinstance(verify, Exception)
    assert isinstance(review.content, ReviewOutput)
    assert [(f.file_path, f.line, f.category.value) for f in review.content.findings] == [
        ("app/billing/discount.py", 3, "readability"),
        ("app/billing/discount.py", 7, "correctness"),
    ]
    assert (review.model, review.tokens_used) == ("script", 1200)
    assert isinstance(verify.content, VerificationOutput)
    assert [(v.id, v.verdict.value) for v in verify.content.verdicts] == [
        ("F1", "keep"),
        ("F2", "drop"),
    ]


def test_a_review_entry_carries_rule_side_and_summary(tmp_path: Path) -> None:
    summary = {"problem": "Плохо.", "positives": ["тесты"], "effort": "день"}
    entry = {
        "review": [
            {"path": "a.py", "line": 2, "message": "м", "rule": ["имена", 1], "side": "old"}
        ],
        "summary": summary,
    }

    (answer,) = load_llm_script(_script(tmp_path, [entry])).answers

    assert not isinstance(answer, Exception)
    content = answer.content
    assert isinstance(content, ReviewOutput)
    (finding,) = content.findings
    assert finding.rule is not None and (finding.rule.name, finding.rule.check) == ("имена", 1)
    assert finding.side.value == "old"
    assert (finding.category.value, finding.severity.value) == ("readability", "low")
    assert content.summary.problem == "Плохо." and content.summary.positives == ("тесты",)


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
    (answer,) = load_llm_script(_script(tmp_path, [{"error": kind, "tokens": 5}])).answers

    assert type(answer) is error_type
    assert isinstance(answer, LlmError) and answer.tokens_used == 5


@pytest.mark.parametrize(
    ("entries", "fragment"),
    [
        ({"review": []}, "ждали JSON-список"),
        ([{"review": [], "error": "timeout"}], "запись 0: нужен ровно один"),
        ([{"verify": []}, {"error": "boom"}], "запись 1: error - один из"),
        ([{"review": [{"path": "a.py", "line": 0, "message": "м"}]}], "line - целое"),
        ([{"review": [{"path": "a.py", "line": True, "message": "м"}]}], "line - целое"),
        ([{"review": [{"path": "a.py", "line": 1, "message": "м", "x": 1}]}], "лишние ключи"),
        ([{"review": [{"path": "a.py", "line": 1, "message": "м", "severity": "x"}]}], "severity"),
        ([{"review": [{"path": "a.py", "line": 1, "message": "м", "rule": ["r"]}]}], "rule"),
        ([{"review": [], "summary": {"problem": "п"}}], "summary не прошла"),
        ([{"verify": [["G1", "keep"]]}], "id кандидата"),
        ([{"verify": [["F1", "maybe"]]}], "вердикт - один из keep, drop"),
        ([{"error": "timeout", "tokens": -1}], "tokens"),
        (["review"], "запись 0: ждали объект"),
    ],
)
def test_an_invalid_script_is_a_script_error(tmp_path: Path, entries: Any, fragment: str) -> None:
    with pytest.raises(ScriptError, match=fragment):
        load_llm_script(_script(tmp_path, entries))


def test_a_non_json_script_is_a_script_error(tmp_path: Path) -> None:
    path = tmp_path / "script.json"
    path.write_text("[", encoding="utf-8")

    with pytest.raises(ScriptError, match="не JSON"):
        load_llm_script(path)


# --- сбои GitHub --------------------------------------------------------------


@pytest.fixture
def github() -> FakeGitHub:
    fake = FakeGitHub("acme/payments", 1, app_private_key())
    fake.open_pull(1, base_sha="b1", head_sha="h1", head_files={"a.py": "x\n"})
    fake.open_pull(2, base_sha="b2", head_sha="h2", head_files={"a.py": "y\n"})
    return fake


def test_review_faults_queue_one_failure_per_spec(github: FakeGitHub) -> None:
    for spec in ("review-502-stored", "review-502", "review-rate-limit", "review-502"):
        github.apply_fault(spec, [1, 2])

    assert github.create_review_failures == [
        CreateReviewFailure(502, stored=True),
        CreateReviewFailure(502, stored=False),
        CreateReviewFailure(
            429, stored=False, headers={"Retry-After": "1"}, message="secondary rate limit"
        ),
        CreateReviewFailure(502, stored=False),
    ]


def test_status_faults_set_their_knobs(github: FakeGitHub) -> None:
    github.apply_fault("graphql-500", [1])
    github.apply_fault("review-comments-500", [1])

    assert (github.graphql_status, github.review_comments_status) == (500, 500)


def test_foreign_review_lands_on_every_pr(github: FakeGitHub) -> None:
    github.apply_fault("foreign-review", [1, 2])

    for number in (1, 2):
        (review,) = github.reviews_of(number)
        assert (review.user_login, review.user_type) == ("reviewer", "User")


def test_latency_takes_seconds(github: FakeGitHub) -> None:
    github.apply_fault("latency=0.25", [])

    assert github.latency_seconds == 0.25


@pytest.mark.parametrize("spec", ["latency=-1", "latency=abc", "latency=nan", "latency="])
def test_bad_latency_is_a_fault_error(github: FakeGitHub, spec: str) -> None:
    with pytest.raises(FaultError, match="SEC"):
        github.apply_fault(spec, [])


def test_unknown_fault_lists_the_valid_names(github: FakeGitHub) -> None:
    with pytest.raises(FaultError, match="допустимы") as error:
        github.apply_fault("review-503", [1])

    assert all(name in str(error.value) for name in FAULTS)
