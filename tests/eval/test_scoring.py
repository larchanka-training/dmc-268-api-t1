"""Сопоставление и метрики eval - без модели, базы и сети."""

import pytest

from .scoring import (
    LINE_WINDOW,
    CaseScore,
    ExpectedIssue,
    Reported,
    aggregate,
    match,
    score_case,
    spread,
)

PATH = "app/domain/discount.py"
RULE = ("Error Handling Standards", 1)
RAISE = ExpectedIssue("raise", PATH, 14, rule=RULE)
SQL = ExpectedIssue("sql", "app/users.py", 2, category="security")


def test_window_is_the_one_from_the_eval_methodology() -> None:
    assert LINE_WINDOW == 3


@pytest.mark.parametrize(
    ("reported", "matched"),
    [
        (Reported(PATH, "new", 14, "correctness", RULE), True),
        (Reported(PATH, "new", 17, "readability"), True),
        (Reported(PATH, "new", 11, "readability"), True),
        (Reported(PATH, "new", 18, "correctness", RULE), False),
        (Reported(PATH, "old", 14, "correctness", RULE), False),
        (Reported("app/domain/other.py", "new", 14, "correctness", RULE), False),
    ],
    ids=["exact", "window-below", "window-above", "outside-window", "old-side", "other-file"],
)
def test_rule_issue_matches_by_file_side_and_window_whatever_the_category(
    reported: Reported, matched: bool
) -> None:
    assert bool(match([RAISE], [reported])) is matched


def test_generic_issue_needs_the_same_category() -> None:
    assert match([SQL], [Reported("app/users.py", "new", 2, "correctness")]) == []
    assert match([SQL], [Reported("app/users.py", "new", 4, "security")]) != []


def test_one_finding_matches_at_most_one_issue() -> None:
    near = ExpectedIssue("near", PATH, 15, rule=("Logging", 1))
    (pair,) = match([RAISE, near], [Reported(PATH, "new", 14, "readability")])
    assert pair[0] is RAISE


def test_the_finding_naming_the_issue_wins_over_a_closer_one() -> None:
    """Точная ссылка на пункт важнее расстояния: соседнее замечание не уводит пару."""
    print_issue = ExpectedIssue("print", PATH, 15, rule=("Logging", 1))
    raise_finding = Reported(PATH, "new", 15, "correctness", RULE)
    print_finding = Reported(PATH, "new", 16, "readability", ("Logging", 1))

    pairs = match([RAISE, print_issue], [raise_finding, print_finding])

    assert {(e.id, r.line) for e, r in pairs} == {("raise", 15), ("print", 16)}


def test_case_score_splits_rule_and_generic_issues_and_counts_attribution() -> None:
    reported = [
        Reported(PATH, "new", 14, "correctness", ("Logging", 1)),
        Reported("app/users.py", "new", 30, "security"),
    ]

    score = score_case([RAISE, SQL], reported)

    assert score == CaseScore(
        tp=1,
        fp=1,
        fn=1,
        rule_tp=1,
        rule_fn=0,
        generic_tp=0,
        generic_fn=1,
        attributed=0,
        missed=("sql",),
    )


def test_negative_case_turns_every_finding_into_a_false_positive() -> None:
    score = score_case([], [Reported(PATH, "new", 3, "readability")])
    assert (score.tp, score.fp, score.fn) == (0, 1, 0)


def test_aggregate_micro_averages_over_cases() -> None:
    perfect = score_case([RAISE], [Reported(PATH, "new", 14, "correctness", RULE)])
    noisy = score_case([SQL], [Reported(PATH, "new", 1, "readability")])

    metrics = aggregate([perfect, noisy])

    assert (metrics.tp, metrics.fp, metrics.fn) == (1, 1, 1)
    assert metrics.precision == pytest.approx(0.5)
    assert metrics.recall == pytest.approx(0.5)
    assert metrics.f1 == pytest.approx(0.5)
    assert (metrics.rule_recall, metrics.generic_recall, metrics.attribution) == (1.0, 0.0, 1.0)


def test_metrics_without_a_denominator_are_absent_not_zero() -> None:
    metrics = aggregate([score_case([], [])])
    assert (metrics.precision, metrics.recall, metrics.f1, metrics.attribution) == (
        None,
        None,
        None,
        None,
    )


def test_expected_issue_needs_exactly_one_label() -> None:
    with pytest.raises(ValueError, match="ровно одна"):
        ExpectedIssue("both", PATH, 1, rule=RULE, category="security")
    with pytest.raises(ValueError, match="ровно одна"):
        ExpectedIssue("none", PATH, 1)


def test_spread_skips_repetitions_without_a_value() -> None:
    assert spread([0.5, None, 1.0]) == (0.75, 0.25)
    assert spread([None]) is None
