"""BENCHMARK_REPORT.md из артефакта живого прогона золотого датасета - без сети и модели.

    uv run python -m tests.eval.benchmark_report --artifact DIR --out BENCHMARK_REPORT.md
        [--dataset DIR]

Раннер офлайн-eval (`evaluate_dataset`) считает метрики и пороги §6 по `pass1/` и
`pass2/` артефакта и пишет их отчёты в `pass1-report/` и `pass2-report/`; генератор
сводит их с `timings.json`, `meta.json` и `verdicts/` в один отчёт «до и после
второго прохода». Раскладка артефакта и состав отчёта - design D3 и D6 change
`add-live-gold-benchmark`.

Пороги на живой модели - измерение: непройденный отмечается в отчёте, но код выхода
от него не зависит (0, или 2 - артефакт не по контракту датасета).

Вывод детерминирован: ни текущего времени (дата - из `meta.json`), ни абсолютных
путей, порядок - кейсы датасета и повторы, числа округлены до двух знаков. Ручные
разделы (между маркерами `manual:begin` и `manual:end` в HTML-комментариях)
переносятся из прежнего файла по порядку. Отчёт строят чистые функции, файлы читает
и пишет только `main`.
"""

import argparse
import json
import math
import re
import statistics
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.application.llm_schema import FindingOutput, ReviewOutput, VerificationOutput
from app.domain.enums import ReviewRunStage, VerifierVerdict
from app.domain.untrusted import sanitize

from .dataset import DATASET_DIR, DatasetError, GoldCase
from .gold_report import cell, num, to_json, to_markdown, write_reports
from .runner import (
    EXIT_DATASET_ERROR,
    EXIT_OK,
    Evaluation,
    Gate,
    ResponseResult,
    Summary,
    evaluate_dataset,
)
from .scoring import OutputCounts, match

PASSES = ("pass1", "pass2")
MANUAL_BEGIN = "<!-- manual:begin -->"
MANUAL_END = "<!-- manual:end -->"
_MANUAL = re.compile(
    re.escape(MANUAL_BEGIN) + r".*?" + re.escape(MANUAL_END), re.DOTALL
)

MAX_DROPPED_FP = 5
MAX_DROPPED_TP = 3
MAX_KEPT_TP = 3
# Ширина корзины гистограммы времени - первая из «круглых», при которой корзин не больше лимита.
HISTOGRAM_STEPS = (1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600, 1200, 3600)
HISTOGRAM_MAX_BINS = 10

FPR_DEFINITION = (
    "**FPR (по кейсам)** — доля валидных ответов на negative-кейсах хотя бы с одним замечанием"
    " = 1 − Abstention; сбойные прогоны — в Valid-output."
)
FDR_DEFINITION = "**FDR** = FP/(TP+FP) = 1 − Precision."

type Verdicts = Mapping[tuple[str, int], str]


def _complement(value: float | None) -> float | None:
    return None if value is None else 1 - value


def _pair(passes: Sequence[Evaluation], value: Callable[[Summary], str]) -> str:
    return " | ".join(value(p.summary) for p in passes)


def _outputs(counts: OutputCounts) -> str:
    total = counts.first_try + counts.after_retry + counts.invalid
    return f"{num(counts.valid_rate)} ({counts.first_try}/{total})"


def _chart(
    title: str, labels: Sequence[str], axis: str, top: float, series: Sequence[str]
) -> list[str]:
    """Блок Mermaid `xychart-beta`: ось x - подписи, ось y - явный диапазон `0 --> top`."""
    return [
        "```mermaid",
        "xychart-beta",
        f'    title "{title}"',
        f"    x-axis {json.dumps(list(labels), ensure_ascii=False)}",
        f'    y-axis "{axis}" 0 --> {top:g}',
        *(f"    {line}" for line in series),
        "```",
    ]


def _values(values: Iterable[float | None]) -> str:
    """Значения серии: нет данных - 0 на графике, «—» остаётся в таблице."""
    return "[" + ", ".join(f"{v or 0:.2f}" for v in values) + "]"


def _configuration(meta: Mapping[str, Any]) -> list[str]:
    run_id, server, repo = (
        meta.get("github_run_id"),
        meta.get("github_server_url"),
        meta.get("github_repository"),
    )
    if run_id and server and repo:
        run = f"[{run_id}]({server}/{repo}/actions/runs/{run_id})"
    else:
        run = cell(None if run_id is None else str(run_id))

    def short(key: str) -> str:
        value = meta.get(key)
        return f"`{value[:12]}`" if isinstance(value, str) else "—"

    return [
        "## Конфигурация",
        "",
        "| Параметр | Значение |",
        "|---|---|",
        f"| Модель | {cell(meta.get('model'))} |",
        f"| Reasoning effort | {cell(meta.get('reasoning_effort'))} |",
        f"| Хэш промптов | {short('prompts_sha256')} |",
        f"| Хэш датасета | {short('dataset_sha256')} |",
        f"| Кейсов | {cell(str(meta.get('cases', '—')))} |",
        f"| Повторов | {cell(str(meta.get('repetitions', '—')))} |",
        f"| Дата прогона | {cell(meta.get('date'))} |",
        f"| Run | {run} |",
    ]


def _metrics(passes: Sequence[Evaluation], calls: OutputCounts) -> list[str]:
    def counts(s: Summary) -> str:
        m = s.metrics
        return f"{m.tp}/{m.tp_required}/{m.fp}/{m.fn}"

    def rate(s: Summary) -> str:
        return f"{num(s.outputs.valid_rate)} ({s.outputs.first_try}/{s.outputs.first_try + s.outputs.invalid})"

    rows = [
        ("Precision", lambda s: num(s.metrics.precision)),
        ("Recall (must_find)", lambda s: num(s.metrics.recall)),
        ("F1", lambda s: num(s.metrics.f1)),
        ("FDR", lambda s: num(_complement(s.metrics.precision))),
        ("FPR (по кейсам)", lambda s: num(_complement(s.abstention))),
        ("Abstention на негативных", lambda s: num(s.abstention)),
        ("Duplicate rate", lambda s: num(s.duplicate_rate)),
        ("Valid-output (файлы ответов)", rate),
        ("TP/TP(must)/FP/FN", counts),
    ]
    p1, p2 = (p.summary for p in passes)
    return [
        "## Метрики до и после второго прохода",
        "",
        (
            "pass1 — ответ первого прохода модели до отсева; pass2 — замечания прогона после отсева"
            " и верификатора."
        ),
        "",
        "| Метрика | pass1 | pass2 |",
        "|---|---|---|",
        *(f"| {name} | {_pair(passes, value)} |" for name, value in rows),
        f"| Valid-output (вызовы модели) | {_outputs(calls)} | — |",
        "",
        f"- {FPR_DEFINITION}",
        f"- {FDR_DEFINITION}",
        "- Пороговым (§6) из них считается только Abstention.",
        (
            "- Valid-output файлов ответов: заглушка сбойного прогона не проходит схему и входит в"
            " невалидные. Valid-output вызовов модели — по шагам прогона (первая попытка против"
            " повтора и провала), общий для обоих проходов."
        ),
        "",
        *_chart(
            "Метрики: столбцы - pass1, линия - pass2",
            ("Precision", "Recall", "F1", "Abstention"),
            "Доля",
            1,
            [
                f"{mark} {_values((s.metrics.precision, s.metrics.recall, s.metrics.f1, s.abstention))}"
                for mark, s in (("bar", p1), ("line", p2))
            ],
        ),
    ]


def _status(gate: Gate) -> str:
    if not gate.active:
        return "не действует"
    return "пройден" if gate.passed else "**не пройден**"


def _gates(passes: Sequence[Evaluation]) -> list[str]:
    lines = [
        "## Пороги LLM_EVAL §6",
        "",
        (
            "Пороги на живой модели — измерение: непройденный порог отмечен, но код выхода генератора"
            " и workflow от него не зависит; решение по порогам — отдельным PR."
        ),
        "",
        "| Гейт | Порог | pass1 | Статус pass1 | pass2 | Статус pass2 | Примечание |",
        "|---|---|---|---|---|---|---|",
    ]
    for g1, g2 in zip(passes[0].gates, passes[1].gates, strict=True):
        if g1.note == g2.note:
            note = g1.note
        else:
            note = "; ".join(
                f"{n}: {g.note}" for n, g in zip(PASSES, (g1, g2)) if g.note
            )
        lines.append(
            f"| {g1.metric} | {g1.threshold} | {num(g1.value)} | {_status(g1)} | "
            f"{num(g2.value)} | {_status(g2)} | {cell(note)} |"
        )
    return lines


def _categories(passes: Sequence[Evaluation]) -> list[str]:
    p1, p2 = ({s.category: s for s in p.summary.categories} for p in passes)
    names = sorted(p1.keys() | p2.keys())

    def value(slices: Mapping[str, Any], name: str, attr: str) -> str:
        return num(getattr(slices[name], attr)) if name in slices else "—"

    lines = [
        "## По категориям",
        "",
        "Срез меньше порога значимости LLM_EVAL §3 — «недостаточно данных», в гейты не входит.",
        "",
        "| Категория | Precision pass1 | Recall pass1 | Precision pass2 | Recall pass2 | Данных |",
        "|---|---|---|---|---|---|",
    ]
    for name in names:
        sufficient = (p2.get(name) or p1[name]).sufficient
        lines.append(
            f"| `{name}` | {value(p1, name, 'precision')} | {value(p1, name, 'recall')} | "
            f"{value(p2, name, 'precision')} | {value(p2, name, 'recall')} | "
            f"{'достаточно' if sufficient else 'недостаточно'} |"
        )
    shown = [p2[n] for n in names if n in p2]
    if shown:
        lines += [
            "",
            *_chart(
                "pass2 по категориям: столбцы - Precision, линия - Recall",
                [s.category for s in shown],
                "Доля",
                1,
                [
                    f"bar {_values(s.precision for s in shown)}",
                    f"line {_values(s.recall for s in shown)}",
                ],
            ),
        ]
    return lines


def _injection(passes: Sequence[Evaluation]) -> list[str]:
    lines = ["## Injection-сьют", ""]
    suites = [[r for r in p.results if r.injection_passed is not None] for p in passes]
    if not suites[0]:
        return [*lines, "Injection-кейсов в наборе нет."]

    def verdict(r: ResponseResult) -> str:
        return (
            "пройден"
            if r.injection_passed
            else "**провал**: " + cell(r.injection_failure)
        )

    lines += ["| Кейс | Повтор | pass1 | pass2 |", "|---|---|---|---|"]
    lines += [
        f"| `{r1.case_id}` | {r1.repetition} | {verdict(r1)} | {verdict(r2)} |"
        for r1, r2 in zip(*suites, strict=True)
    ]
    lines.append(
        "| Итого | | "
        + _pair(passes, lambda s: f"{s.injection_passed}/{s.injection_responses}")
        + " |"
    )
    return lines


def _f1_spread(passes: Sequence[Evaluation]) -> list[str]:
    p1, p2 = (p.summary for p in passes)
    lines = [
        "## Разброс F1 по повторам",
        "",
        "| Повтор | F1 pass1 | F1 pass2 |",
        "|---|---|---|",
    ]
    lines += [
        f"| {rep} | {num(f1)} | {num(f2)} |"
        for rep, f1, f2 in zip(
            p1.repetitions, p1.f1_by_repetition, p2.f1_by_repetition, strict=True
        )
    ]
    lines.append(f"| Разброс | {num(p1.f1_spread)} | {num(p2.f1_spread)} |")
    return lines


def _stats(name: str, values: Sequence[float]) -> str:
    """Среднее, медиана и p95 (ближайший ранг: значение из выборки, без интерполяции)."""
    ordered = sorted(values)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
    return (
        f"| {name} | {len(ordered)} | {num(statistics.fmean(ordered))} | "
        f"{num(statistics.median(ordered))} | {num(p95)} |"
    )


def _histogram(durations: Sequence[float]) -> list[str]:
    top = max(durations)
    width = next(
        (w for w in HISTOGRAM_STEPS if int(top // w) + 1 <= HISTOGRAM_MAX_BINS),
        HISTOGRAM_STEPS[-1],
    )
    bins = int(top // width) + 1
    counts = [0] * bins
    for value in durations:
        counts[min(int(value // width), bins - 1)] += 1
    return _chart(
        "Время прогона: число прогонов по корзинам, с",
        [f"{i * width}–{(i + 1) * width}" for i in range(bins)],
        "Прогонов",
        max(counts),
        ["bar [" + ", ".join(str(c) for c in counts) + "]"],
    )


def _timings(timings: Sequence[Mapping[str, Any]]) -> list[str]:
    lines = [
        "## Время ревью",
        "",
        (
            "`duration_seconds` прогона (от захвата до `completed`) и сумма шагов этапа из"
            " `timings.json`; прогон без времени не учитывается."
        ),
        "",
    ]
    durations = [
        float(t["duration_seconds"])
        for t in timings
        if t.get("duration_seconds") is not None
    ]
    order = [stage.value for stage in ReviewRunStage]
    stages = sorted(
        {s for t in timings for s in t.get("steps") or {}},
        key=lambda s: (order.index(s) if s in order else len(order), s),
    )
    if durations or stages:
        lines += [
            "| Что | Прогонов | Среднее, с | Медиана, с | p95, с |",
            "|---|---|---|---|---|",
        ]
        if durations:
            lines.append(_stats("Прогон целиком", durations))
        for stage in stages:
            values = [
                float(t["steps"][stage])
                for t in timings
                if stage in (t.get("steps") or {})
            ]
            lines.append(_stats(f"`{stage}`", values))
    else:
        lines.append("Времени прогонов в артефакте нет.")

    tokens = [
        int(t["tokens_used"]) for t in timings if t.get("tokens_used") is not None
    ]
    rejected = sum(int(t.get("rejected_findings") or 0) for t in timings)
    dropped = sum(int(t.get("verifier_dropped_findings") or 0) for t in timings)
    lines += [
        "",
        "- "
        + (
            f"Токены: всего {sum(tokens)}, в среднем на прогон {num(statistics.fmean(tokens))}"
            f" (прогонов с данными: {len(tokens)})."
            if tokens
            else "Токенов в артефакте нет."
        ),
        f"- Отсеяно до верификатора: {rejected}, отброшено верификатором: {dropped}.",
    ]
    if durations:
        lines += ["", *_histogram(durations)]
    return lines


def _failures(timings: Sequence[Mapping[str, Any]]) -> list[str]:
    failed = sorted(
        (t for t in timings if t.get("status") != "completed"),
        key=lambda t: (str(t["case_id"]), int(t["repetition"])),
    )
    lines = ["## Сбои прогонов", ""]
    if not failed:
        return [*lines, "Сбоев нет: все прогоны дошли до `completed`."]
    lines += [
        "Сбойный прогон в pass1 и pass2 — заглушка, она входит в невалидные ответы.",
        "",
        "| Кейс | Повтор | Статус | Причина |",
        "|---|---|---|---|",
    ]
    lines += [
        f"| `{t['case_id']}` | {t['repetition']} | {cell(t.get('status'))} | {cell(t.get('failure'))} |"
        for t in failed
    ]
    return lines


def _findings(text: str) -> tuple[FindingOutput, ...] | None:
    try:
        return ReviewOutput.model_validate_json(text).findings
    except ValidationError:
        return None


def _key(f: FindingOutput) -> tuple[str, str, int, str]:
    """Одно и то же замечание до и после второго прохода - по якорю и категории.

    Сообщение конвейер переписывает (`redact_secrets`, снятие префикса атрибуции),
    поэтому оно только для показа. Путь модель видела после `sanitize`, а в базе он
    исходный: сравниваются оба в виде после `sanitize`.
    """
    return (sanitize(f.file_path), f.side.value, f.line, f.category.value)


def _true_positives(case: GoldCase, result: ResponseResult) -> list[bool]:
    """По замечанию ответа: совпало ли оно с размеченным нарушением (правило LLM_EVAL §2)."""
    pairs = match(case.expected_issues, result.reported)
    return [any(r is matched for _, matched in pairs) for r in result.reported]


def _drop_reasons(text: str | None) -> str:
    if text is None:
        return "—"
    try:
        verdicts = VerificationOutput.model_validate_json(text).verdicts
    except ValidationError:
        return "—"
    reasons = [
        f"{v.id}: {v.reason}" for v in verdicts if v.verdict is VerifierVerdict.DROP
    ]
    return "; ".join(reasons) or "—"


def _examples(passes: Sequence[Evaluation], verdicts: Verdicts) -> list[str]:
    cases = {c.case_id: c for c in passes[0].cases}
    pass2 = {(r.case_id, r.repetition): r for r in passes[1].results}
    texts = [
        {(c.case_id, r.repetition): r.text for c in p.cases for r in c.responses}
        for p in passes
    ]
    dropped_fp: list[str] = []
    dropped_tp: list[str] = []
    kept_tp: list[str] = []

    def row(r: ResponseResult, f: FindingOutput, tail: str) -> str:
        anchor = f"`{f.file_path}:{f.side.value}:{f.line}`"
        return (
            f"| `{r.case_id}` | {r.repetition} | {anchor} | `{f.category.value}` | "
            f"{cell(f.message)} | {tail} |"
        )

    for r1 in passes[0].results:
        key = (r1.case_id, r1.repetition)
        first = _findings(texts[0][key])
        second = _findings(texts[1][key])
        r2 = pass2[key]
        if first is not None and second is not None:
            remaining = Counter(_key(f) for f in second)
            reasons = cell(_drop_reasons(verdicts.get(key)))
            for f, tp in zip(
                first, _true_positives(cases[r1.case_id], r1), strict=True
            ):
                if remaining[_key(f)]:
                    remaining[_key(f)] -= 1
                elif tp:
                    dropped_tp.append(row(r1, f, f"отсеяно ошибочно | {reasons}"))
                else:
                    dropped_fp.append(row(r1, f, f"отсеяно верно | {reasons}"))
        if second is not None:
            for f, tp in zip(
                second, _true_positives(cases[r2.case_id], r2), strict=True
            ):
                if tp:
                    kept_tp.append(row(r2, f, "TP"))

    head = "| Кейс | Повтор | Якорь | Категория | Сообщение модели |"
    lines = [
        "## Примеры",
        "",
        (
            "Кейсы — из синтетического золотого датасета. Цитата — сообщение модели и якорь"
            " `путь:сторона:строка`, код диффа не приводится. «Отсеяно верно» — замечание первого"
            " прохода, не совпавшее с разметкой (FP), которого нет после второго; «отсеяно"
            " ошибочно» — такое же, но совпавшее (TP). Вердикты drop — все отброшенные"
            " верификатором кандидаты этого прогона."
        ),
        "",
        "### Отсеяно вторым проходом",
        "",
    ]
    dropped = dropped_fp[:MAX_DROPPED_FP] + dropped_tp[:MAX_DROPPED_TP]
    if dropped:
        lines += [
            head + " Итог | Вердикты drop прогона |",
            "|---|---|---|---|---|---|---|",
        ]
        lines += dropped
    else:
        lines.append("Второй проход ничего не отсеял.")
    lines += ["", "### Осталось после второго прохода (TP)", ""]
    if kept_tp:
        lines += [head + " Итог |", "|---|---|---|---|---|---|"]
        lines += kept_tp[:MAX_KEPT_TP]
    else:
        lines.append("Совпавших с разметкой замечаний после второго прохода нет.")
    return lines


LIMITATIONS = [
    "## Ограничения",
    "",
    (
        "- Датасет синтетический: кейсы написаны под метрики, а не взяты из живых репозиториев;"
        " перенос цифр на прод — допущение."
    ),
    (
        "- AST-контекст есть только у добавленных файлов: у изменённых полного файла в датасете нет,"
        " модель видит меньше, чем в проде."
    ),
    (
        "- LLM-судья не применялся: совпадение — по якорю и категории (LLM_EVAL §2), смысл"
        " сообщения не оценивается."
    ),
    (
        "- FPR считается только по negative-кейсам; срезы по категориям малы — см. «недостаточно"
        " данных»."
    ),
    "- Сводка pass2 взята из pass1: проверка injection в тексте сводки видит сказанное моделью.",
]


def render(
    passes: Sequence[Evaluation],
    timings: Sequence[Mapping[str, Any]],
    meta: Mapping[str, Any],
    verdicts: Verdicts,
) -> str:
    """Отчёт целиком, с ручными разделами-заглушками; переносит их `keep_manual`."""
    calls = sum(
        (OutputCounts(**t["outputs"]) for t in timings if t.get("outputs")),
        OutputCounts(),
    )
    sections = [
        [
            "# Бенчмарк живой модели на золотом датасете",
            "",
            (
                "Сгенерировано `python -m tests.eval.benchmark_report` из артефакта живого прогона:"
                " правки вне ручных разделов перезапишутся при регенерации."
            ),
        ],
        _configuration(meta),
        _metrics(passes, calls),
        _gates(passes),
        _categories(passes),
        _injection(passes),
        _f1_spread(passes),
        _timings(timings),
        _failures(timings),
        _examples(passes, verdicts),
        LIMITATIONS,
        [
            "## Итоги smoke релизной сборки",
            "",
            MANUAL_BEGIN,
            "_Заполняется вручную по `docs/testing/RELEASE_SMOKE.md`._",
            MANUAL_END,
        ],
        ["## Выводы", "", MANUAL_BEGIN, "_Заполняется вручную._", MANUAL_END],
    ]
    return "\n\n".join("\n".join(s) for s in sections) + "\n"


def keep_manual(generated: str, previous: str | None) -> str:
    """Ручные разделы прежнего файла - на места разделов нового, по порядку.

    Лишние прежние разделы дописываются в конец: ручной текст не теряется молча.
    """
    if previous is None:
        return generated
    old = _MANUAL.findall(previous)
    if not old:
        return generated
    blocks = iter(old)
    merged = _MANUAL.sub(lambda m: next(blocks, m.group(0)), generated)
    rest = list(blocks)
    return merged if not rest else merged + "\n" + "\n\n".join(rest) + "\n"


def _read_verdicts(directory: Path) -> dict[tuple[str, int], str]:
    found: dict[tuple[str, int], str] = {}
    if directory.is_dir():
        for path in sorted(directory.glob("*/run-*.json")):
            repetition = int(path.stem.removeprefix("run-"))
            found[(path.parent.name, repetition)] = path.read_text(encoding="utf-8")
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests.eval.benchmark_report",
        description="BENCHMARK_REPORT.md из артефакта живого прогона золотого датасета.",
    )
    parser.add_argument(
        "--artifact", type=Path, required=True, help="каталог артефакта (D3)"
    )
    parser.add_argument("--out", type=Path, required=True, help="куда записать отчёт")
    parser.add_argument(
        "--dataset", type=Path, default=DATASET_DIR, help="каталог датасета"
    )
    args = parser.parse_args(argv)

    try:
        passes = [
            evaluate_dataset(args.dataset, args.artifact / name) for name in PASSES
        ]
    except DatasetError as error:
        print(f"Артефакт не по контракту датасета: {error}", file=sys.stderr)
        return EXIT_DATASET_ERROR
    for name, evaluation in zip(PASSES, passes, strict=True):
        write_reports(
            args.artifact / f"{name}-report",
            to_json(*evaluation.report_args()),
            to_markdown(*evaluation.report_args()),
        )

    timings = json.loads((args.artifact / "timings.json").read_text(encoding="utf-8"))
    meta = json.loads((args.artifact / "meta.json").read_text(encoding="utf-8"))
    verdicts = _read_verdicts(args.artifact / "verdicts")
    previous = args.out.read_text(encoding="utf-8") if args.out.is_file() else None
    report = keep_manual(render(passes, timings, meta, verdicts), previous)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(report, encoding="utf-8")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
