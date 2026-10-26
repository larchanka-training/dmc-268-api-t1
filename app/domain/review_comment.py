"""Текст комментариев, публикуемых на хостинг: инлайн-замечание и сводка."""

from app.domain.entities import Finding
from app.domain.enums import FindingCategory, FindingSeverity

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


def format_summary_comment(problem: str, positives: tuple[str, ...], effort: str) -> str:
    lines = [problem, ""]
    if positives:
        lines.append("**Сделано хорошо**")
        lines.extend(f"- {item}" for item in positives)
        lines.append("")
    lines.append(f"**Объём доработки:** {effort}")
    return "\n".join(lines)

