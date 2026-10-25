"""Повторный прогон: говорит ли уже тред бота о той же проблеме, что находка.

Место треда - его строка на хостинге сейчас, а не номер из прошлого прогона:
код над строкой могли дописать, и тред переехал вместе с ней. Устаревший тред
(outdated, строки нет) ни с чем не совпадает - к нынешнему коду он не привязан.

Решение команды (#169, п. 3а): тред, закрытый человеком, замечание не
повторяет; исключение - критичное, ставшее серьёзнее. Тред, закрытый ботом как
исправленный, ничего не покрывает: проблема вернулась.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from app.domain.entities import BotThread, Finding
from app.domain.enums import FindingSeverity

_RANK: dict[FindingSeverity, int] = {
    FindingSeverity.LOW: 0,
    FindingSeverity.MEDIUM: 1,
    FindingSeverity.HIGH: 2,
    FindingSeverity.CRITICAL: 3,
}


@dataclass(frozen=True, slots=True)
class ThreadCoverage:
    # Тред, который уже говорит об этой находке: своего комментария ей не нужно.
    covered_by: BotThread | None


def thread_coverage(
    finding: Finding,
    threads: Iterable[BotThread],
    current_lines: Mapping[str, int | None],
) -> ThreadCoverage:
    """Чем покрыта находка; `current_lines` - строка треда на хостинге по корню."""
    line = finding.anchor.line
    if line is None:
        return ThreadCoverage(covered_by=None)
    same_place = [
        thread
        for thread in threads
        if thread.finding.anchor.file_path == finding.anchor.file_path
        and thread.finding.anchor.side is finding.anchor.side
        and thread.finding.category is finding.category
        and current_lines.get(thread.root_provider_comment_id) == line
    ]
    open_threads = [t for t in same_place if not t.resolved]
    for thread in open_threads:
        if _RANK[thread.finding.severity] >= _RANK[finding.severity]:
            return ThreadCoverage(covered_by=thread)
    for thread in same_place:
        if thread.resolved and not thread.resolved_by_bot and not _escalated_to_critical(
            finding, thread
        ):
            return ThreadCoverage(covered_by=thread)
    return ThreadCoverage(covered_by=None)


def _escalated_to_critical(finding: Finding, thread: BotThread) -> bool:
    return (
        finding.severity is FindingSeverity.CRITICAL
        and thread.finding.severity is not FindingSeverity.CRITICAL
    )
