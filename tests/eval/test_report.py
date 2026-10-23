"""Отчёт eval: агрегирование по повторам и вид таблицы - без модели."""

from .report import RunResult, config_metrics, to_json, to_markdown
from .scoring import ExpectedIssue, Reported, score_case

ISSUE = ExpectedIssue("c/raise", "app/x.py", 5, rule=("Errors", 1))
HIT = Reported("app/x.py", "new", 5, "correctness", ("Errors", 1))
META = {"model": "m-1", "prompts_sha256": "ab" * 32, "repetitions": 2, "cases": 1}


def _result(config: str, rep: int, reported: tuple[Reported, ...]) -> RunResult:
    return RunResult(config, "c", rep, "completed", None, reported, score_case([ISSUE], reported))


RESULTS = [
    _result("rules", 1, (HIT,)),
    _result("rules", 2, ()),
    _result("absent", 1, ()),
    _result("absent", 2, ()),
    RunResult("absent", "c", 3, "failed", "модель не ответила", (), None),
]


def test_metrics_are_computed_per_repetition_and_skip_failed_runs() -> None:
    rules = config_metrics(RESULTS, "rules")
    assert [m.recall for m in rules] == [1.0, 0.0]
    absent = config_metrics(RESULTS, "absent")
    assert [m.cases for m in absent] == [1, 1, 0]


def test_markdown_shows_mean_and_spread_per_config_and_lists_failures() -> None:
    text = to_markdown(RESULTS, ["absent", "rules"], META)
    assert "| Метрика | `absent` | `rules` |" in text
    assert "| Recall | 0.00 ± 0.00 | 0.50 ± 0.50 |" in text
    assert "| Верная ссылка на пункт | — | 1.00 ± 0.00 |" in text
    assert "| `c` | `rules` | `c/raise` | 0 |" in text
    assert "- `c` в `absent`, повтор 3: failed, модель не ответила" in text


def test_json_keeps_the_meta_the_window_and_every_run() -> None:
    data = to_json(RESULTS, ["rules"], META)
    assert data["model"] == "m-1"
    assert data["line_window"] == 3
    assert len(data["configs"]["rules"]) == 2
    assert len(data["runs"]) == len(RESULTS)
