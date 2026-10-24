"""Текст комментариев, публикуемых на хостинг (шапка severity/category и ответы в тред)."""

from app.domain.entities import Finding
from app.domain.enums import (
    FindingCategory,
    FindingSeverity,
    ThreadFollowUpStatus,
)

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


def format_thread_reply(
    status: ThreadFollowUpStatus,
    reply: str,
    *,
    severity: FindingSeverity,
) -> str:
    """Ответ бота в существующий тред на повторном прогоне."""
    if status == ThreadFollowUpStatus.FIXED:
        prefix = "✅ Исправление подтверждено, обсуждение закрыто"
    elif status == ThreadFollowUpStatus.STILL_RELEVANT:
        prefix = "⏳ Замечание ещё актуально"
    elif status == ThreadFollowUpStatus.ACCEPTED_RISK:
        prefix = "🤝 Аргументация принята к сведению"
    else:
        prefix = "ℹ️ Замечание снято (код изменился)"

    body = f"{prefix}\n\n{reply.strip()}"
    if status == ThreadFollowUpStatus.ACCEPTED_RISK and severity in (
        FindingSeverity.CRITICAL,
        FindingSeverity.HIGH,
    ):
        body += (
            "\n\n*Замечания уровня critical/major автоматически не закрываются "
            "по аргументации — при согласии закройте обсуждение вручную.*"
        )
    return body
