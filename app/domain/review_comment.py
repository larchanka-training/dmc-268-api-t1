"""Текст комментариев, публикуемых на хостинг (шапка severity/category и ответы в тред)."""

from app.domain.entities import DiffAnchor, Finding
from app.domain.enums import (
    DiffSide,
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
    """Инлайн-комментарий к строке: шапка + тело + рекомендация."""
    header = (
        f"⚠️ Потенциальная проблема | {_SEVERITY_LABEL[finding.severity]} | "
        f"{_CATEGORY_LABEL[finding.category]}"
    )
    parts = [header, "", finding.message]
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


def anchor_matches_llm(
    anchor: DiffAnchor,
    *,
    file_path: str,
    side: DiffSide,
    line: int,
) -> bool:
    """Сопоставить якорь сохранённого замечания с полями thread follow-up от LLM."""
    line_on_side = anchor.new_line if side is DiffSide.NEW else anchor.old_line
    return anchor.file_path == file_path and anchor.side is side and line_on_side == line
