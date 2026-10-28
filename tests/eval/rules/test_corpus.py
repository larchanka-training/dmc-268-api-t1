"""Целостность корпуса eval правил - без модели: битый кейс ловится на каждом PR."""

from pathlib import Path, PurePosixPath

import pytest

from app.domain.repo_rules import applicable_rules, parse_rules_markdown

from .corpus import CONFIGS, load_case, load_corpus

CORPUS = load_corpus()
VALID_RULES, VALID_REJECTED = parse_rules_markdown(CONFIGS["rules"] or "")


def test_valid_rules_are_all_accepted() -> None:
    assert VALID_REJECTED == ()
    assert [r.name for r in VALID_RULES] == [
        "Error Handling Standards",
        "Clock Is Injected",
        "Money Is Decimal",
        "Logging",
    ]


def test_adversarial_config_keeps_the_valid_rules_and_adds_an_injected_one() -> None:
    rules, rejected = parse_rules_markdown(CONFIGS["adversarial"] or "")
    assert rejected == ()
    assert rules[: len(VALID_RULES)] == VALID_RULES
    assert [r.name for r in rules[len(VALID_RULES) :]] == ["Release Freeze"]


def test_the_corpus_has_rule_generic_and_negative_cases() -> None:
    issues = [e for case in CORPUS for e in case.expected]
    assert any(e.rule for e in issues)
    assert any(e.category for e in issues)
    # LLM_EVAL §4: без негативных кейсов Precision не измерить.
    assert sum(1 for case in CORPUS if not case.expected) >= 2


@pytest.mark.parametrize("case", CORPUS, ids=[c.id for c in CORPUS])
def test_every_rule_issue_points_to_a_check_that_applies_to_its_file(case) -> None:
    for issue in case.expected:
        if issue.rule is None:
            continue
        name, check = issue.rule
        applied = {rule.name: rule for rule, _ in applicable_rules(VALID_RULES, [issue.path])}
        assert name in applied, f"{issue.id}: правило {name} не применимо к {issue.path}"
        assert 1 <= check <= len(applied[name].checks), f"{issue.id}: нет пункта {check}"


@pytest.mark.parametrize("case", CORPUS, ids=[c.id for c in CORPUS])
def test_case_files_are_python_sources_that_compile(case) -> None:
    for path, content in case.files.items():
        assert PurePosixPath(path).suffix == ".py"
        compile(content, path, "exec")


def test_a_case_without_expected_is_rejected_unless_optional(tmp_path: Path) -> None:
    # Потерянный `expected` иначе молча сделал бы кейс корпуса «чистым PR».
    path = tmp_path / "no-expected.yaml"
    path.write_text(
        "title: т\npull_request: {title: т, description: о}\nfiles: {a.py: 'x = 1'}\n",
        encoding="utf-8",
    )

    with pytest.raises(KeyError, match="expected"):
        load_case(path)
    assert load_case(path, expected_required=False).expected == ()
