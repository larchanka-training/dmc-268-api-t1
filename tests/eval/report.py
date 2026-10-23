"""Отчёт eval правил: результаты прогонов → JSON и Markdown. Чистые функции."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

from .scoring import LINE_WINDOW, CaseScore, Metrics, Reported, aggregate, spread


@dataclass(frozen=True, slots=True)
class RunResult:
    """Один PR корпуса в одной конфигурации правил, один повтор."""

    config: str
    case: str
    repetition: int
    status: str
    failure: str | None
    reported: tuple[Reported, ...]
    score: CaseScore | None


_METRICS = (
    ("precision", "Precision"),
    ("recall", "Recall"),
    ("f1", "F1"),
    ("rule_recall", "Recall по правилам"),
    ("generic_recall", "Recall по общим дефектам"),
    ("attribution", "Верная ссылка на пункт"),
)


def config_metrics(results: Sequence[RunResult], config: str) -> list[Metrics]:
    """Метрики конфигурации по каждому повтору; упавшие прогоны в метрики не входят."""
    repetitions = sorted({r.repetition for r in results if r.config == config})
    return [
        aggregate(
            r.score
            for r in results
            if r.config == config and r.repetition == rep and r.score is not None
        )
        for rep in repetitions
    ]


def _cell(values: Sequence[float | None]) -> str:
    summary = spread(values)
    if summary is None:
        return "—"
    avg, deviation = summary
    return f"{avg:.2f}" if len(values) == 1 else f"{avg:.2f} ± {deviation:.2f}"


def to_json(
    results: Sequence[RunResult], configs: Sequence[str], meta: dict[str, Any]
) -> dict[str, Any]:
    return {
        **meta,
        "line_window": LINE_WINDOW,
        "configs": {
            config: [asdict(m) for m in config_metrics(results, config)] for config in configs
        },
        "runs": [asdict(r) for r in results],
    }


def to_markdown(results: Sequence[RunResult], configs: Sequence[str], meta: dict[str, Any]) -> str:
    lines = [
        "# Eval правил ревью: Precision/Recall",
        "",
        f"Модель: `{meta['model']}`.",
        f"Хэш промптов: `{meta['prompts_sha256'][:12]}`.",
        f"Повторов: {meta['repetitions']}, кейсов: {meta['cases']}, окно строки: ±{LINE_WINDOW}.",
        "",
        "| Метрика | " + " | ".join(f"`{c}`" for c in configs) + " |",
        "|---|" + "---|" * len(configs),
    ]
    per_config = {c: config_metrics(results, c) for c in configs}
    for key, title in _METRICS:
        cells = [_cell([getattr(m, key) for m in per_config[c]]) for c in configs]
        lines.append(f"| {title} | " + " | ".join(cells) + " |")
    totals = [
        "/".join(str(sum(getattr(m, k) for m in per_config[c])) for k in ("tp", "fp", "fn"))
        for c in configs
    ]
    lines.append("| TP/FP/FN, сумма повторов | " + " | ".join(totals) + " |")

    lines += ["", "## Пропуски и шум по кейсам", ""]
    lines += ["| Кейс | Конфигурация | Пропущено | Лишних замечаний |", "|---|---|---|---|"]
    for r in results:
        if r.score is None:
            continue
        if r.score.missed or r.score.fp:
            missed = ", ".join(f"`{m}`" for m in r.score.missed) or "—"
            lines.append(f"| `{r.case}` | `{r.config}` | {missed} | {r.score.fp} |")

    failed = [r for r in results if r.score is None]
    if failed:
        lines += ["", "## Прогоны, не дошедшие до публикации", ""]
        lines += [
            f"- `{r.case}` в `{r.config}`, повтор {r.repetition}: {r.status}, {r.failure}"
            for r in failed
        ]
    return "\n".join(lines) + "\n"
