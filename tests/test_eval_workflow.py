"""Workflow eval: метка PR не запускает платный золотой набор (design D7 add-live-gold-benchmark).

Job `rules` - eval правил владельца, по метке `eval` и вручную; job `gold` - живой
бенчмарк золотого датасета, только вручную с `suite` gold или all. Пороги на живой
модели - измерение: отчёт собирается и выгружается и при упавшем pytest.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW = Path(".github/workflows/eval.yml")

# Лексемы выражения `if:` GitHub Actions, которые понимает `_runs`; остальное - ошибка.
_TOKEN = re.compile(r"\s*(?:(\(|\)|&&|\|\||==|!=)|'([^']*)'|([A-Za-z_][\w.-]*))")
_OPERATORS = {"(": "(", ")": ")", "&&": " and ", "||": " or ", "==": "==", "!=": "!="}
# Событие запуска: у PR нет inputs (пустая строка), у ручного запуска нет метки (null).
_REPO = "larchanka-training/dmc-268-api-t1"
_BASE = {
    "github.event_name": "",
    "github.event.label.name": None,
    "github.event.pull_request.head.repo.full_name": None,
    "github.repository": _REPO,
    "inputs.suite": "",
}


def _workflow() -> dict[Any, Any]:
    data: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data


def _jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = _workflow()["jobs"]
    return jobs


def _step(job: dict[str, Any], needle: str) -> dict[str, Any]:
    (step,) = [
        s for s in job["steps"] if needle in str(s.get("run", s.get("uses", "")))
    ]
    found: dict[str, Any] = step
    return found


def _runs(condition: str, context: dict[str, Any]) -> bool:
    """Значение `if:` джоба на контексте: только `&&`, `||`, `==`, `!=`, скобки, строки, имена.

    Неизвестный синтаксис или имя контекста - ошибка теста, а не молчаливое `False`.
    """
    python: list[str] = []
    position = 0
    text = condition.strip()
    while position < len(text):
        token = _TOKEN.match(text, position)
        if token is None or token.end() == position:
            raise AssertionError(f"неподдержанный синтаксис в if: {text[position:]!r}")
        operator, literal, name = token.groups()
        if operator is not None:
            python.append(_OPERATORS[operator])
        elif literal is not None:
            python.append(repr(literal))
        else:
            if name not in context:
                raise AssertionError(f"неизвестное имя контекста в if: {name}")
            python.append(repr(context[name]))
        position = token.end()
    return bool(eval("".join(python), {"__builtins__": {}}))  # только литералы и операторы из _OPERATORS


def _pull(label: str, *, fork: bool = False) -> dict[str, Any]:
    head = "someone/dmc-268-api-t1" if fork else _REPO
    return _BASE | {
        "github.event_name": "pull_request",
        "github.event.label.name": label,
        "github.event.pull_request.head.repo.full_name": head,
    }


def _dispatch(suite: str) -> dict[str, Any]:
    return _BASE | {"github.event_name": "workflow_dispatch", "inputs.suite": suite}


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (_pull("eval"), {"rules"}),
        (_pull("bug"), set()),
        (_pull("eval", fork=True), set()),
        (_dispatch("rules"), {"rules"}),
        (_dispatch("gold"), {"gold"}),
        (_dispatch("all"), {"rules", "gold"}),
    ],
    ids=[
        "pr-eval",
        "pr-other-label",
        "pr-fork-eval",
        "suite-rules",
        "suite-gold",
        "suite-all",
    ],
)
def test_jobs_started_by_each_trigger(
    context: dict[str, Any], expected: set[str]
) -> None:
    started = {name for name, job in _jobs().items() if _runs(job["if"], context)}

    assert started == expected


def test_condition_evaluator_rejects_unknown_syntax() -> None:
    with pytest.raises(AssertionError, match="неподдержанный"):
        _runs("!(inputs.suite == 'gold')", _BASE)
    with pytest.raises(AssertionError, match="неизвестное имя"):
        _runs("github.ref == 'main'", _BASE)


def test_suite_input_defaults_to_rules() -> None:
    # PyYAML читает ключ `on` как True.
    suite = _workflow()[True]["workflow_dispatch"]["inputs"]["suite"]

    assert suite["type"] == "choice"
    assert suite["options"] == ["rules", "gold", "all"]
    assert suite["default"] == "rules"


def test_rules_job_runs_only_the_rules_corpus() -> None:
    rules = _jobs()["rules"]

    assert _step(rules, "pytest")["run"].endswith("-m eval tests/eval/rules")


def test_gold_job_measures_reports_and_uploads_even_on_failure() -> None:
    gold = _jobs()["gold"]

    assert gold["timeout-minutes"] >= _jobs()["rules"]["timeout-minutes"]
    assert gold["env"]["EVAL_REPORT_DIR"] == "eval-live-gold"
    assert gold["env"]["EVAL_REPETITIONS"] == "${{ inputs.repetitions }}"
    pytest_step = _step(gold, "pytest")
    assert pytest_step["env"]["PYTEST_FAIL_ON_SKIP"] == "1"
    assert pytest_step["run"].endswith("-m eval tests/eval/test_live_gold.py")
    report = _step(gold, "make benchmark-report")
    assert report["if"] == "always()"
    assert "EVAL_LIVE_DIR=eval-live-gold" in report["run"]
    summary = _step(gold, "GITHUB_STEP_SUMMARY")
    assert f"steps.{report['id']}.outcome == 'success'" in summary["if"]
    upload = _step(gold, "actions/upload-artifact")
    assert upload["if"] == "always()"
    assert upload["with"]["path"].split() == ["eval-live-gold/", "BENCHMARK_REPORT.md"]


def test_gold_job_reuses_services_secrets_and_pins_of_rules() -> None:
    rules, gold = _jobs()["rules"], _jobs()["gold"]

    assert gold["services"] == rules["services"]
    shared = (
        "TEST_DATABASE_URL",
        "TEST_RABBITMQ_URL",
        "EUROROUTER_API_KEYS",
        "EUROROUTER_BASE_URL",
    )
    assert {k: gold["env"][k] for k in shared} == {k: rules["env"][k] for k in shared}
    assert [s["uses"] for s in gold["steps"] if "uses" in s] == [
        s["uses"] for s in rules["steps"] if "uses" in s
    ]
    model_files = [
        s["run"] for s in rules["steps"] if "eurorouter-" in s.get("run", "")
    ]
    assert len(model_files) == 2
    assert [
        s["run"] for s in gold["steps"] if "eurorouter-" in s.get("run", "")
    ] == model_files
    assert gold["steps"][0]["with"]["persist-credentials"] is False
    assert _workflow()["permissions"] == {"contents": "read"}
