"""Отчёт eval: агрегирование по повторам и вид таблицы - без модели."""

from .report import RunResult, config_metrics, config_outputs, to_json, to_markdown
from .scoring import CaseScore, ExpectedIssue, OutputCounts, Reported, score_case

ISSUE = ExpectedIssue("c/raise", "app/x.py", 5, rule=("Errors", 1), categories=("correctness",))
HIT = Reported("app/x.py", "new", 5, "correctness", ("Errors", 1))
META = {"model": "m-1", "prompts_sha256": "ab" * 32, "repetitions": 2, "cases": 1}


def _result(
    config: str, rep: int, reported: tuple[Reported, ...], outputs: OutputCounts
) -> RunResult:
    return RunResult(
        config, "c", rep, "completed", None, reported, score_case([ISSUE], reported), outputs
    )


RESULTS = [
    _result("rules", 1, (HIT,), OutputCounts(first_try=2)),
    _result("rules", 2, (), OutputCounts(first_try=1, after_retry=1)),
    _result("absent", 1, (), OutputCounts(first_try=1)),
    _result("absent", 2, (), OutputCounts(first_try=1)),
    RunResult(
        "absent", "c", 3, "failed", "модель не ответила", (), None, OutputCounts(invalid=1)
    ),
]


def test_metrics_are_computed_per_repetition_and_skip_failed_runs() -> None:
    rules = config_metrics(RESULTS, "rules")
    assert [m.recall for m in rules] == [1.0, 0.0]
    absent = config_metrics(RESULTS, "absent")
    assert [m.cases for m in absent] == [1, 1, 0]


def test_markdown_shows_mean_and_spread_per_config_and_lists_failures() -> None:
    text = to_markdown(RESULTS, ["absent", "rules"], META)
    assert "| Метрика | `absent` | `rules` |" in text
    assert "| Recall | 0.00 ± 0.00 (2/3) | 0.50 ± 0.50 |" in text
    assert "| Верная ссылка на пункт | — | 1.00 ± 0.00 (1/2) |" in text
    assert "| `c` | `rules` | `c/raise` | 0 |" in text
    assert "- `c` в `absent`, повтор 3: failed, модель не ответила" in text


def test_json_keeps_the_meta_the_window_and_every_run() -> None:
    data = to_json(RESULTS, ["rules"], META)
    assert data["model"] == "m-1"
    assert data["line_window"] == 3
    assert len(data["configs"]["rules"]) == 2
    assert len(data["runs"]) == len(RESULTS)


def test_valid_output_rate_is_per_repetition_and_counts_failed_runs() -> None:
    assert [o.valid_rate for o in config_outputs(RESULTS, "absent")] == [1.0, 1.0, 0.0]
    text = to_markdown(RESULTS, ["absent", "rules"], META)
    assert "| Валидно с первой попытки | 0.67 ± 0.47 | 0.75 ± 0.25 |" in text
    assert (
        "| Вызовы: первая/повтор/невалидно/без ответа, сумма повторов | 2/0/1/0 | 3/1/0/0 |"
        in text
    )


def test_a_config_without_model_answers_shows_a_dash() -> None:
    results = [RunResult("rules", "c", 1, "failed", "таймаут", (), None, OutputCounts(no_answer=1))]
    assert "| Валидно с первой попытки | — |" in to_markdown(results, ["rules"], META)


def test_json_keeps_the_outputs_per_config_and_per_run() -> None:
    data = to_json(RESULTS, ["rules"], META)
    assert data["outputs"]["rules"][1] == {
        "first_try": 1,
        "after_retry": 1,
        "invalid": 0,
        "no_answer": 0,
        "valid_rate": 0.5,
    }
    assert data["runs"][0]["outputs"]["first_try"] == 2


def _counted(rep: int, tp: int, fp: int, fn: int) -> RunResult:
    score = CaseScore(tp, fp, fn, tp, fn, 0, 0, tp, (), tp_required=tp, rule_matched=tp)
    return RunResult("rules", "c", rep, "completed", None, (), score, OutputCounts(first_try=1))


def test_a_repetition_that_found_nothing_pulls_f1_down_instead_of_dropping_out() -> None:
    # Пример с ревью: F1 0.89, 0.89 и повтор с TP=0, FP=0, FN=5 - среднее не 0.89.
    results = [_counted(1, 4, 0, 1), _counted(2, 4, 0, 1), _counted(3, 0, 0, 5)]
    assert "| F1 | 0.59 ± 0.42 |" in to_markdown(results, ["rules"], META)


def test_a_cell_names_how_many_repetitions_had_a_value() -> None:
    # Верная ссылка не определена в повторе без совпавших нарушений правил.
    results = [_counted(1, 1, 0, 0), _counted(2, 1, 0, 0), _counted(3, 0, 0, 1)]
    text = to_markdown(results, ["rules"], META)
    assert "| Верная ссылка на пункт | 1.00 ± 0.00 (2/3) |" in text
    assert "| Recall | 0.67 ± 0.47 |" in text
    single = to_markdown([_counted(1, 1, 0, 0)], ["rules"], META)
    assert "| Верная ссылка на пункт | 1.00 |" in single
