"""Отчёт офлайн-eval золотого датасета: JSON и Markdown. Чистые функции, кроме записи.

Отчёт детерминирован: ни времени, ни абсолютных путей - один и тот же датасет
даёт байт в байт тот же файл, и разница двух отчётов в PR - разница метрик.
Метаданные (хэши промптов и датасета) приходят снаружи.
"""

import json
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .dataset import GoldCase
from .runner import (
    ABSTENTION_MIN_NEGATIVE_CASES,
    SLICE_MIN_CASES,
    SLICE_MIN_FINDINGS,
    Gate,
    ResponseResult,
    Summary,
)
from .scoring import LINE_WINDOW

JSON_NAME = "report.json"
MARKDOWN_NAME = "report.md"


def _cell(text: str | None) -> str:
    """Текст в ячейку таблицы Markdown: `|` и перевод строки ломали бы строку таблицы."""
    return (text or "—").replace("|", "\\|").replace("\n", " ")


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def _meta(cases: Sequence[GoldCase], summary: Summary, meta: dict[str, Any]) -> dict[str, Any]:
    kinds: dict[str, int] = {}
    for case in cases:
        kinds[case.kind] = kinds.get(case.kind, 0) + 1
    return {
        **meta,
        "line_window": LINE_WINDOW,
        "cases": len(cases),
        "kinds": dict(sorted(kinds.items())),
        "responses": summary.outputs.first_try + summary.outputs.invalid,
        "repetitions": list(summary.repetitions),
    }


def _result_json(r: ResponseResult) -> dict[str, Any]:
    return {
        "case_id": r.case_id,
        "kind": r.kind,
        "repetition": r.repetition,
        "valid": r.valid,
        "error": r.error,
        "findings": len(r.reported),
        "score": None if r.score is None else asdict(r.score),
        "categories": {c: asdict(n) for c, n in sorted(r.categories.items())},
        "duplicates": r.duplicates,
        "over_limit": r.over_limit,
        "injection_passed": r.injection_passed,
        "injection_failure": r.injection_failure,
    }


def to_json(
    cases: Sequence[GoldCase],
    results: Sequence[ResponseResult],
    summary: Summary,
    checked: Sequence[Gate],
    meta: dict[str, Any],
) -> dict[str, Any]:
    return {
        "meta": _meta(cases, summary, meta),
        "passed": all(g.passed for g in checked if g.active),
        "metrics": asdict(summary.metrics),
        "valid_output_rate": summary.outputs.valid_rate,
        "outputs": asdict(summary.outputs),
        "findings": summary.findings,
        "duplicates": summary.duplicates,
        "duplicate_rate": summary.duplicate_rate,
        "categories": [
            {
                "category": s.category,
                **asdict(s.counts),
                "precision": s.precision,
                "recall": s.recall,
                "cases": s.cases,
                "expected": s.expected,
                "sufficient": s.sufficient,
            }
            for s in summary.categories
        ],
        "abstention": {
            "negative_cases": summary.negative_cases,
            "responses": summary.negative_responses,
            "silent": summary.silent_responses,
            "rate": summary.abstention,
        },
        "injection": {
            "responses": summary.injection_responses,
            "passed": summary.injection_passed,
        },
        "f1_by_repetition": list(summary.f1_by_repetition),
        "f1_spread": summary.f1_spread,
        "over_limit": list(summary.over_limit),
        "gates": [asdict(g) for g in checked],
        "results": [_result_json(r) for r in results],
    }


def _gate_status(gate: Gate) -> str:
    if not gate.active:
        return "не действует"
    return "пройден" if gate.passed else "**не пройден**"


def to_markdown(
    cases: Sequence[GoldCase],
    results: Sequence[ResponseResult],
    summary: Summary,
    checked: Sequence[Gate],
    meta: dict[str, Any],
) -> str:
    info = _meta(cases, summary, meta)
    m = summary.metrics
    kinds = ", ".join(f"{k} {n}" for k, n in info["kinds"].items())
    passed = all(g.passed for g in checked if g.active)
    verdict = "все действующие пороги пройдены" if passed else "есть непройденные пороги"
    lines = [
        "# Офлайн-eval золотого датасета",
        "",
        "Зафиксированные ответы модели, без вызова модели. Итог: " + verdict + ".",
        "",
        (
            f"Хэш промптов: `{info['prompts_sha256'][:12]}`, хэш датасета: "
            f"`{info['dataset_sha256'][:12]}`."
        ),
        (
            f"Кейсов: {info['cases']} ({kinds}), ответов: {info['responses']}, "
            f"повторов: {len(info['repetitions'])}, окно строки: ±{LINE_WINDOW}."
        ),
        "",
        "## Метрики",
        "",
        "| Метрика | Значение |",
        "|---|---|",
        f"| Precision | {_num(m.precision)} |",
        f"| Recall (must_find) | {_num(m.recall)} |",
        f"| F1 | {_num(m.f1)} |",
        f"| TP/TP(must)/FP/FN | {m.tp}/{m.tp_required}/{m.fp}/{m.fn} |",
        (
            f"| Valid-output rate | {_num(summary.outputs.valid_rate)} "
            f"({summary.outputs.first_try}/{summary.outputs.first_try + summary.outputs.invalid}) |"
        ),
        (
            f"| Duplicate rate | {_num(summary.duplicate_rate)} "
            f"({summary.duplicates}/{summary.findings}) |"
        ),
        (
            f"| Abstention на негативных | {_num(summary.abstention)} "
            f"({summary.silent_responses}/{summary.negative_responses}) |"
        ),
        f"| Injection-сьют | {summary.injection_passed}/{summary.injection_responses} |",
        f"| Разброс F1 между повторами | {_num(summary.f1_spread)} |",
        "",
        "## Пороги (LLM_EVAL §6)",
        "",
        "| Гейт | Значение | Порог | Статус | Примечание |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {g.metric} | {_num(g.value)} | {g.threshold} | {_gate_status(g)} | {_cell(g.note)} |"
        for g in checked
    ]
    lines += [
        "",
        (
            f"Abstention - процентом с {ABSTENTION_MIN_NEGATIVE_CASES} негативных кейсов, до того -"
            " «все негативные молчат»."
        ),
        "",
        "## По категориям",
        "",
        (
            f"Срез меньше {SLICE_MIN_CASES} кейсов или {SLICE_MIN_FINDINGS} размеченных находок - "
            "«недостаточно данных» (LLM_EVAL §3)."
        ),
        "",
        "| Категория | Precision | Recall | TP/TP(must)/FP/FN | Кейсов | Обязательных | Данных |",
        "|---|---|---|---|---|---|---|",
    ]
    lines += [
        f"| `{s.category}` | {_num(s.precision)} | {_num(s.recall)} | "
        f"{s.counts.tp}/{s.counts.tp_required}/{s.counts.fp}/{s.counts.fn} | {s.cases} | "
        f"{s.expected} | "
        f"{'достаточно' if s.sufficient else 'недостаточно'} |"
        for s in summary.categories
    ]

    suite = [r for r in results if r.injection_passed is not None]
    if suite:
        lines += ["", "## Injection-сьют", "", "| Кейс | Повтор | Итог |", "|---|---|---|"]
        lines += [
            f"| `{r.case_id}` | {r.repetition} | "
            f"{'пройден' if r.injection_passed else '**провал**: ' + _cell(r.injection_failure)} |"
            for r in suite
        ]

    lines += [
        "",
        "## По кейсам",
        "",
        "| Кейс | Вид | Повтор | Валиден | Замечаний | TP/TP(must)/FP/FN | Пропущено | Сверх max |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        if r.score is None:
            lines.append(
                f"| `{r.case_id}` | {r.kind} | {r.repetition} | нет: {_cell(r.error)} | — | — | — | — |"
            )
            continue
        missed = ", ".join(f"`{m.split('/', 1)[1]}`" for m in r.score.missed) or "—"
        lines.append(
            f"| `{r.case_id}` | {r.kind} | {r.repetition} | да | {len(r.reported)} | "
            f"{r.score.tp}/{r.score.tp_required}/{r.score.fp}/{r.score.fn} | {missed} | "
            f"{'да' if r.over_limit else '—'} |"
        )
    return "\n".join(lines) + "\n"


def write_reports(report_dir: Path, report: dict[str, Any], markdown: str) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / JSON_NAME).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (report_dir / MARKDOWN_NAME).write_text(markdown, encoding="utf-8")
