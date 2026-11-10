"""Сопоставление и метрики eval - без модели, базы и сети."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.domain.entities import ReviewRunStep
from app.domain.enums import ReviewRunStage, ReviewRunStepStatus
from app.domain.llm_errors import LlmOutputError

from .scoring import (
    LINE_WINDOW,
    MAX_EXPECTED_PER_CASE,
    CaseScore,
    ExpectedIssue,
    OutputCounts,
    Reported,
    aggregate,
    classify_calls,
    match,
    score_case,
    spread,
)

PATH = "app/domain/discount.py"
RULE = ("Error Handling Standards", 1)
RAISE = ExpectedIssue("raise", PATH, 14, rule=RULE, categories=("correctness",))
SQL = ExpectedIssue("sql", "app/users.py", 2, category="security")


def test_window_is_the_one_from_the_eval_methodology() -> None:
    assert LINE_WINDOW == 3


@pytest.mark.parametrize(
    ("reported", "matched"),
    [
        (Reported(PATH, "new", 14, "correctness", RULE), True),
        (Reported(PATH, "new", 17, "correctness"), True),
        (Reported(PATH, "new", 11, "correctness"), True),
        (Reported(PATH, "new", 14, "readability", RULE), True),
        (Reported(PATH, "new", 14, "readability"), False),
        (Reported(PATH, "new", 18, "correctness", RULE), False),
        (Reported(PATH, "old", 14, "correctness", RULE), False),
        (Reported("app/domain/other.py", "new", 14, "correctness", RULE), False),
    ],
    ids=[
        "exact",
        "window-below",
        "window-above",
        "same-rule-other-category",
        "foreign-category-without-rule",
        "outside-window",
        "old-side",
        "other-file",
    ],
)
def test_rule_issue_matches_by_file_side_window_and_rule_or_allowed_category(
    reported: Reported, matched: bool
) -> None:
    assert bool(match([RAISE], [reported])) is matched


def test_unrelated_finding_near_a_rule_violation_is_not_a_hit() -> None:
    """Кейс 01: замечание о читаемости рядом с `raise` - не находка нарушения пункта."""
    raise_percent = ExpectedIssue("raise-percent", PATH, 16, rule=RULE, categories=("correctness",))

    score = score_case([raise_percent], [Reported(PATH, "new", 17, "readability")])

    assert (score.tp, score.fp, score.fn) == (0, 1, 1)


def test_matching_is_optimal_not_greedy() -> None:
    """Кейс 02: жадный выбор отдал бы ближайшую находку float, и print остался бы без пары."""
    allowed = ("correctness", "readability")
    float_money = ExpectedIssue(
        "float-money", PATH, 15, rule=("Money Is Decimal", 1), categories=allowed
    )
    print_issue = ExpectedIssue("print", PATH, 17, rule=("Logging", 1), categories=allowed)
    reported = [
        Reported(PATH, "new", 16, "readability"),
        Reported(PATH, "new", 13, "readability"),
    ]

    score = score_case([float_money, print_issue], reported)

    assert (score.tp, score.fp, score.fn) == (2, 0, 0)
    assert {(e.id, r.line) for e, r in match([float_money, print_issue], reported)} == {
        ("float-money", 13),
        ("print", 16),
    }


def test_among_equal_pair_counts_the_same_meaning_wins() -> None:
    near = ExpectedIssue("near", PATH, 15, rule=("Logging", 1), categories=("correctness",))
    naming_near = Reported(PATH, "new", 14, "correctness", ("Logging", 1))

    (pair,) = match([RAISE, near], [naming_near])

    assert pair[0] is near


def test_among_equal_meaning_the_shorter_distance_wins() -> None:
    reported = [
        Reported(PATH, "new", 16, "correctness"),
        Reported(PATH, "new", 15, "correctness"),
    ]

    (pair,) = match([RAISE], reported)

    assert pair[1].line == 15


def test_a_case_with_too_many_expected_issues_is_rejected() -> None:
    issues = [
        ExpectedIssue(f"i{n}", PATH, n, category="security")
        for n in range(MAX_EXPECTED_PER_CASE + 1)
    ]
    with pytest.raises(ValueError, match="эталонных"):
        match(issues, [])


def test_generic_issue_needs_the_same_category() -> None:
    assert match([SQL], [Reported("app/users.py", "new", 2, "correctness")]) == []
    assert match([SQL], [Reported("app/users.py", "new", 4, "security")]) != []


def test_one_finding_matches_at_most_one_issue() -> None:
    near = ExpectedIssue("near", PATH, 15, rule=("Logging", 1), categories=("correctness",))
    (pair,) = match([RAISE, near], [Reported(PATH, "new", 14, "correctness")])
    assert pair[0] is RAISE


def test_the_finding_naming_the_issue_wins_over_a_closer_one() -> None:
    """Точная ссылка на пункт важнее расстояния: соседнее замечание не уводит пару."""
    print_issue = ExpectedIssue("print", PATH, 15, rule=("Logging", 1), categories=("readability",))
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


def test_rule_issue_needs_allowed_categories_and_generic_one_must_not_have_them() -> None:
    with pytest.raises(ValueError, match="categories"):
        ExpectedIssue("rule", PATH, 1, rule=RULE)
    with pytest.raises(ValueError, match="categories"):
        ExpectedIssue("generic", PATH, 1, category="security", categories=("security",))


def test_categories_are_values_of_the_finding_category_enum() -> None:
    with pytest.raises(ValueError, match="style"):
        ExpectedIssue("rule", PATH, 1, rule=RULE, categories=("style",))
    with pytest.raises(ValueError, match="style"):
        ExpectedIssue("generic", PATH, 1, category="style")


def test_f1_is_zero_when_nothing_is_found_and_absent_only_without_any_count() -> None:
    blind = CaseScore(
        tp=0,
        fp=0,
        fn=5,
        rule_tp=0,
        rule_fn=5,
        generic_tp=0,
        generic_fn=0,
        attributed=0,
        missed=(),
    )
    assert aggregate([blind]).f1 == 0.0
    assert aggregate([score_case([], [])]).f1 is None


def test_spread_skips_repetitions_without_a_value() -> None:
    assert spread([0.5, None, 1.0]) == (0.75, 0.25)
    assert spread([None]) is None


RUN_ID = UUID(int=1)
START = datetime(2026, 11, 6, 10, 0, tzinfo=UTC)
INVALID = LlmOutputError.code


def _step(
    stage: ReviewRunStage, error_code: str | None = None, *, tokens: int | None = 10
) -> ReviewRunStep:
    return ReviewRunStep(
        id=uuid4(),
        review_run_id=RUN_ID,
        stage=stage,
        status=ReviewRunStepStatus.FAILED if error_code else ReviewRunStepStatus.COMPLETED,
        started_at=START,
        tokens_used=tokens,
        error_code=error_code,
    )


ANALYSING = ReviewRunStage.ANALYSING
VERIFYING = ReviewRunStage.VERIFYING


@pytest.mark.parametrize(
    ("steps", "expected"),
    [
        ([_step(ANALYSING), _step(VERIFYING)], OutputCounts(first_try=2)),
        (
            [_step(ANALYSING, INVALID), _step(ANALYSING), _step(VERIFYING)],
            OutputCounts(first_try=1, after_retry=1),
        ),
        ([_step(ANALYSING, INVALID), _step(ANALYSING, INVALID)], OutputCounts(invalid=1)),
        # Верификатор ответил по схеме, но не покрыл кандидатов: невалиден.
        ([_step(ANALYSING), _step(VERIFYING, INVALID)], OutputCounts(first_try=1, invalid=1)),
        # Таймаут перед ответом - не повтор по схеме.
        ([_step(ANALYSING, "LLM_TIMEOUT"), _step(ANALYSING)], OutputCounts(first_try=1)),
        ([_step(ANALYSING, "LLM_TIMEOUT")], OutputCounts(no_answer=1)),
        # Первый ответ не прошёл схему, повтор упал на таймауте: вызов невалиден.
        ([_step(ANALYSING, INVALID), _step(ANALYSING, "LLM_TIMEOUT")], OutputCounts(invalid=1)),
        # Без кандидатов верификатор модель не зовёт.
        ([_step(ANALYSING), _step(VERIFYING, tokens=None)], OutputCounts(first_try=1)),
        ([_step(ReviewRunStage.FETCHING_DIFF, "VCS_UNAVAILABLE", tokens=None)], OutputCounts()),
    ],
)
def test_each_model_call_is_classified_by_its_last_step(
    steps: list[ReviewRunStep], expected: OutputCounts
) -> None:
    assert classify_calls(steps) == expected


def test_valid_rate_counts_only_first_try_and_ignores_calls_without_answer() -> None:
    counts = OutputCounts(first_try=6, after_retry=1, invalid=1, no_answer=5)
    assert counts.valid_rate == 0.75
    assert OutputCounts(no_answer=2).valid_rate is None
    assert counts + OutputCounts(invalid=2) == OutputCounts(6, 1, 3, 5)
