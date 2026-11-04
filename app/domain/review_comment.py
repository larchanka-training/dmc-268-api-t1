"""Текст комментариев, публикуемых на хостинг: инлайн-замечание и сводка."""

import re
from collections import Counter

from app.domain.entities import Finding
from app.domain.enums import FindingCategory, FindingSeverity
from app.domain.review_coverage import ReviewCoverage
from app.domain.untrusted import sanitize

_SEVERITY_LABEL: dict[FindingSeverity, str] = {
    FindingSeverity.CRITICAL: "🔴 Критично",
    FindingSeverity.HIGH: "🟠 Существенно",
    FindingSeverity.MEDIUM: "🟡 Заметно",
    FindingSeverity.LOW: "🔵 Незначительно",
}

_CATEGORY_LABEL: dict[FindingCategory, str] = {
    FindingCategory.SECURITY: "Безопасность",
    FindingCategory.CORRECTNESS: "Корректность",
    FindingCategory.PERFORMANCE: "Производительность",
    FindingCategory.READABILITY: "Читаемость",
}


def format_inline_finding_comment(finding: Finding) -> str:
    """Инлайн-комментарий к строке: шапка + тело + рекомендация.

    Замечание по правилу владельца начинается префиксом атрибуции. Его строит
    сервис из проверенной ссылки (`Finding.rule`), а не модель: по комментарию
    видно, чьё правило сработало, и подделать это текстом замечания нельзя.
    """
    header = (
        f"⚠️ Потенциальная проблема | {_SEVERITY_LABEL[finding.severity]} | "
        f"{_CATEGORY_LABEL[finding.category]}"
    )
    message = finding.message
    if finding.rule is not None:
        rule = finding.rule
        message = (
            f"Согласно пользовательскому правилу «{rule.name}» "
            f"(пункт {rule.check}: {rule.check_text}): {message}"
        )
    parts = [header, "", message]
    if finding.suggestion:
        parts.extend(["", "**Рекомендация**", "", finding.suggestion])
    return "\n".join(parts)


def format_summary_comment(
    problem: str,
    positives: tuple[str, ...],
    effort: str,
    *,
    findings: tuple[Finding, ...],
    coverage: ReviewCoverage,
) -> str:
    """Сводка модели, а под ней — риски и файлы, которые строит код (#279)."""
    lines = [problem, ""]
    if positives:
        lines.append("**Сделано хорошо**")
        lines.extend(f"- {item}" for item in positives)
        lines.append("")
    lines.append(f"**Объём доработки:** {effort}")
    risks = _risk_lines(findings)
    if risks:
        lines.extend(["", "**Риски**", *risks])
    files = _file_lines(coverage)
    if files:
        lines.extend(["", *files])
    return "\n".join(lines)


_RISK_SEVERITIES = (FindingSeverity.CRITICAL, FindingSeverity.HIGH)

# Предел GitHub на комментарий — 65 536 символов, а имена файлов пишет автор PR: и число
# путей, и длина каждого ограничены, иначе POST ревью отклонят и не опубликуется ничего.
# 4 группы × 50 путей × ~210 символов — около 42 000.
_MAX_PATHS_PER_GROUP = 50
_MAX_PATH_CHARS = 200


def _risk_lines(findings: tuple[Finding, ...]) -> list[str]:
    counts = Counter((f.category, f.severity) for f in findings if f.severity in _RISK_SEVERITIES)
    lines = []
    for category, label in _CATEGORY_LABEL.items():
        parts = [f"{_SEVERITY_LABEL[s]} — {counts[category, s]}" for s in _RISK_SEVERITIES if counts[category, s]]
        if parts:
            lines.append(f"- {label}: {', '.join(parts)}")
    return lines


def _file_lines(coverage: ReviewCoverage) -> list[str]:
    # Шапка группы в <summary> и подзаголовок списка; порядок — как в сводке.
    groups = [
        group
        for group in (
            ("проверено", "Проверены", coverage.reviewed),
            ("частично", "Частично: часть изменений не вошла в лимит контекста", coverage.partial),
            ("не проверено", "Не проверены: изменения не вошли в лимит контекста", coverage.not_reviewed),
            ("отброшено фильтром", "Отброшены фильтром: lock-файлы, сгенерированные, vendor, бинарные", coverage.filtered),
        )
        if group[2]
    ]
    if not groups:
        return []
    header = ", ".join(f"{count_label} {len(paths)}" for count_label, _, paths in groups)
    lines = ["<details>", f"<summary>Файлы: {header}</summary>", ""]
    for _, title, paths in groups:
        lines.append(f"**{title}**")
        lines.extend(f"- {_code(path)}" for path in paths[:_MAX_PATHS_PER_GROUP])
        if len(paths) > _MAX_PATHS_PER_GROUP:
            lines.append(f"- … и ещё {len(paths) - _MAX_PATHS_PER_GROUP}")
        lines.append("")
    lines.append("</details>")
    return lines


def _code(path: str) -> str:
    """Путь в code span: его пишет автор PR, и разметку из него GitHub не исполнит."""
    # `sanitize` оставляет переводы строк и табуляцию — они разорвали бы пункт списка, а
    # обратная кавычка закрыла бы span: все три — видимой записью, и забор всегда одинарный.
    text = re.sub(r"[\n\t`]", lambda m: f"<U+{ord(m.group()):04X}>", sanitize(path))
    if len(text) > _MAX_PATH_CHARS:
        text = text[:_MAX_PATH_CHARS] + "…"
    return f"`{text}`"

