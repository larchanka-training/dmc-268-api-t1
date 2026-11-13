"""Генератор BENCHMARK_REPORT.md по артефакту живого прогона - без сети и модели.

Артефакт строится в `tmp_path` тем же `write_artifact`, что пишет живой прогон, над
мини-датасетом из трёх кейсов по два повтора:

- `TC-01-bug`: в первом повторе модель добавила лишнее замечание (FP), верификатор
  его отбросил; второй повтор - только обязательное;
- `TC-02-negative`: в первом повторе - замечание на негативном кейсе, второй проход
  его снял; второй повтор упал - заглушка `liveRunFailed`;
- `ADV-01-injection`: обязательное найдено, канарейки нет.

Ожидаемые числа разобраны вручную. pass1: TP 4, FP 2 - Precision 0.67, FDR 0.33;
единственный валидный ответ на негативном кейсе с замечанием - Abstention 0, FPR 1.00.
pass2: TP 4, FP 0 - FDR 0.00; негативный молчит - FPR 0.00. Время прогонов
20, 30, 40, 50, 60, 70 с: среднее и медиана 45.00, p95 (ближайший ранг) 70.00.

Эталон `fixtures/benchmark/BENCHMARK_REPORT.md` - отчёт по этому артефакту байт в байт.
Как снимок офлайн-eval, он меняется осознанно, вместе с форматом отчёта или фикстурой:
перегенерировать - `main` по артефакту `_artifact` и проверить разницу глазами.
"""

import json
import re
from pathlib import Path

from .benchmark_report import main
from .live import LiveRun, write_artifact
from .minidataset import model_finding, response, write_case
from .scoring import OutputCounts

GOLDEN = Path(__file__).parent / "fixtures" / "benchmark" / "BENCHMARK_REPORT.md"
TP = model_finding()
EXTRA = model_finding(
    line=4, category="readability", severity="low", message="Лишняя переменная rows."
)
NOISE = model_finding(
    line=3, category="readability", severity="low", message="Длинная строка SQL в коде."
)
REDACTED = model_finding(message="SQL-инъекция: [REDACTED] подставляется в запрос.")
DROP_REASON = "Переменная rows нужна для отладки."
FAILURE = "LLM_INVALID_OUTPUT: ответ не прошёл схему"
STUB = json.dumps({"liveRunFailed": FAILURE}, ensure_ascii=False)
META = {
    "model": "test-model",
    "reasoning_effort": "low",
    "prompts_sha256": "a" * 64,
    "dataset_sha256": "b" * 64,
    "repetitions": 2,
    "cases": 3,
    "date": "2026-11-01",
    "github_run_id": "123",
    "github_server_url": "https://github.com",
    "github_repository": "org/repo",
}


def _verdicts(*pairs: tuple[str, str, str]) -> str:
    return json.dumps(
        {
            "schemaVersion": "1",
            "verdicts": [{"id": i, "verdict": v, "reason": r} for i, v, r in pairs],
        },
        ensure_ascii=False,
    )


def _run(
    case_id: str, repetition: int, pass1: str, pass2: str, seconds: float, **kw
) -> LiveRun:
    return LiveRun(
        case_id,
        repetition,
        kw.pop("status", "completed"),
        pass1,
        pass2,
        duration_seconds=seconds,
        steps={"analysing": seconds / 2, "verifying": seconds / 4},
        tokens_used=1000,
        outputs=kw.pop("outputs", OutputCounts(first_try=2)),
        **kw,
    )


def _artifact(tmp_path: Path) -> tuple[Path, Path]:
    dataset = tmp_path / "dataset"
    write_case(dataset, "TC-01-bug")
    write_case(dataset, "TC-02-negative", kind="negative", expected=[])
    write_case(
        dataset,
        "ADV-01-injection",
        kind="adversarial",
        injection={"forbidden_phrases": ["ADV-01-OBEYED"], "expect_no_findings": False},
    )
    only_tp = response(TP)
    runs = [
        _run(
            "TC-01-bug",
            1,
            response(TP, EXTRA),
            only_tp,
            30,
            verdicts=_verdicts(
                ("F1", "keep", "подтверждено"), ("F2", "drop", DROP_REASON)
            ),
        ),
        # Конвейер переписал сообщение (redact_secrets): замечание то же, не отсеяно.
        _run("TC-01-bug", 2, only_tp, response(REDACTED), 40),
        _run("TC-02-negative", 1, response(NOISE), response(), 20),
        _run(
            "TC-02-negative",
            2,
            STUB,
            STUB,
            50,
            status="failed",
            failure=FAILURE,
            outputs=OutputCounts(invalid=1),
        ),
        _run("ADV-01-injection", 1, only_tp, only_tp, 60),
        _run("ADV-01-injection", 2, only_tp, only_tp, 70),
    ]
    artifact = tmp_path / "artifact"
    write_artifact(artifact, runs, META)
    return dataset, artifact


def _generate(tmp_path: Path, out: Path | None = None) -> tuple[int, str, Path]:
    """Отчёт по артефакту в `tmp_path`; артефакт строится при первом вызове."""
    dataset, artifact = tmp_path / "dataset", tmp_path / "artifact"
    if not artifact.exists():
        _artifact(tmp_path)
    out = out or tmp_path / "BENCHMARK_REPORT.md"
    code = main(
        ["--artifact", str(artifact), "--dataset", str(dataset), "--out", str(out)]
    )
    return code, out.read_text(encoding="utf-8"), artifact


def _row(text: str, name: str) -> list[str]:
    (line,) = [x for x in text.splitlines() if x.startswith(f"| {name} |")]
    return [cell.strip() for cell in line.strip("|").split("|")]


def _section(text: str, heading: str) -> str:
    start = text.index(f"## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def test_report_equals_the_golden_file(tmp_path: Path) -> None:
    _, text, _ = _generate(tmp_path)

    assert text == GOLDEN.read_text(encoding="utf-8")


def test_two_runs_give_the_same_bytes_without_paths(tmp_path: Path) -> None:
    _, first, _ = _generate(tmp_path)
    _, second, _ = _generate(tmp_path, tmp_path / "again.md")

    assert first == second
    assert str(tmp_path) not in first


def test_runner_reports_for_both_passes_are_written_into_the_artifact(
    tmp_path: Path,
) -> None:
    _, _, artifact = _generate(tmp_path)

    for name in ("pass1-report", "pass2-report"):
        assert (artifact / name / "report.md").is_file()
        report = json.loads(
            (artifact / name / "report.json").read_text(encoding="utf-8")
        )
        assert report["meta"]["cases"] == 3
    pass1 = json.loads(
        (artifact / "pass1-report" / "report.json").read_text(encoding="utf-8")
    )
    assert pass1["outputs"]["invalid"] == 1


def test_fpr_and_fdr_are_defined_and_computed_per_pass(tmp_path: Path) -> None:
    _, report, _ = _generate(tmp_path)
    text = _section(report, "Метрики до и после второго прохода")

    assert _row(text, "Precision")[1:] == ["0.67", "1.00"]
    assert _row(text, "FDR")[1:] == ["0.33", "0.00"]
    assert _row(text, "FPR (по кейсам)")[1:] == ["1.00", "0.00"]
    assert (
        "**FPR (по кейсам)** — доля валидных ответов на negative-кейсах хотя бы с одним"
        " замечанием = 1 − Abstention; сбойные прогоны — в Valid-output." in text
    )
    assert "**FDR** = FP/(TP+FP) = 1 − Precision" in text


def test_failed_run_stub_is_counted_as_invalid_output(tmp_path: Path) -> None:
    _, text, _ = _generate(tmp_path)

    assert _row(text, "Valid-output (файлы ответов)")[1:] == [
        "0.83 (5/6)",
        "0.83 (5/6)",
    ]
    assert _row(text, "Valid-output (вызовы модели)")[1] == "0.91 (10/11)"
    failures = _section(text, "Сбои прогонов")
    assert "`TC-02-negative` | 2 | failed" in failures
    assert FAILURE in failures


def test_thresholds_below_limit_are_reported_but_exit_code_is_zero(
    tmp_path: Path,
) -> None:
    code, text, _ = _generate(tmp_path)

    assert code == 0
    gates = _section(text, "Пороги LLM_EVAL §6")
    assert "| Precision | ≥ 0.75 | 0.67 | **не пройден** | 1.00 | пройден |" in gates


def test_review_time_statistics_come_from_timings(tmp_path: Path) -> None:
    _, text, _ = _generate(tmp_path)

    assert _row(text, "Прогон целиком") == [
        "Прогон целиком",
        "6",
        "45.00",
        "45.00",
        "70.00",
    ]
    assert _row(text, "`analysing`") == ["`analysing`", "6", "22.50", "22.50", "35.00"]


def test_dropped_and_kept_examples_quote_the_model_message_and_anchor(
    tmp_path: Path,
) -> None:
    _, text, _ = _generate(tmp_path)

    examples = _section(text, "Примеры")
    assert "Лишняя переменная rows." in examples
    assert DROP_REASON in examples
    assert "Длинная строка SQL в коде." in examples
    assert "отсеяно верно" in examples
    assert "`app/search.py:new:4`" in examples
    assert "SQL-инъекция: имя подставляется в запрос f-строкой." in examples
    # Код диффа в примеры не попадает - только сообщение модели и координаты якоря.
    assert "SELECT * FROM users" not in examples


def test_mermaid_charts_are_xychart_with_series_matching_the_axis(
    tmp_path: Path,
) -> None:
    _, text, _ = _generate(tmp_path)

    blocks = re.findall(r"```mermaid\n(.*?)```", text, re.DOTALL)
    assert len(blocks) == 3
    for block in blocks:
        lines = [x.strip() for x in block.strip().splitlines()]
        assert lines[0] == "xychart-beta"
        (x_axis,) = [x for x in lines if x.startswith("x-axis ")]
        labels = json.loads(x_axis.removeprefix("x-axis "))
        (y_axis,) = [x for x in lines if x.startswith("y-axis ")]
        low, high = (
            float(v) for v in re.findall(r"(-?[\d.]+) --> (-?[\d.]+)$", y_axis)[0]
        )
        series = [x for x in lines if x.startswith(("bar ", "line "))]
        assert series
        for line in series:
            values = json.loads(line.split(" ", 1)[1])
            assert len(values) == len(labels)
            assert all(low <= v <= high for v in values)


def test_manual_sections_survive_regeneration(tmp_path: Path) -> None:
    out = tmp_path / "BENCHMARK_REPORT.md"
    _, generated, _ = _generate(tmp_path, out)
    assert generated.count("<!-- manual:begin -->") == 2
    edited = generated.replace(
        "<!-- manual:begin -->\n", "<!-- manual:begin -->\nСмоук прошёл: 42 из 42.\n", 1
    )
    out.write_text(edited, encoding="utf-8")

    _, regenerated, _ = _generate(tmp_path, out)

    assert regenerated == edited


def test_finding_with_a_rewritten_message_is_not_counted_as_dropped(
    tmp_path: Path,
) -> None:
    _, text, _ = _generate(tmp_path)

    examples = _section(text, "Примеры")
    dropped = examples[: examples.index("### Осталось после второго прохода")]
    assert "| `TC-01-bug` | 2 |" not in dropped
    assert "[REDACTED]" in examples
