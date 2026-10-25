"""Решение о блокировке мерджа по открытым критичным замечаниям."""

from collections.abc import Iterable

from app.domain.entities import Finding, PublishedComment
from app.domain.enums import CommentKind, FindingSeverity


def merge_blocked_by_findings(open_findings: tuple[Finding, ...]) -> bool:
    return any(f.severity is FindingSeverity.CRITICAL for f in open_findings)


def untracked_findings(
    findings: Iterable[Finding], comments: Iterable[PublishedComment]
) -> tuple[Finding, ...]:
    """Находки опубликованного ревью прогона, у которых нет своего треда в базе.

    Ревью прогона на хостинге (есть сводка), а id inline-комментария находки
    хостинг не отдал: комментарий, скорее всего, в PR, но тред по нему базе не
    известен. Для gate такая находка открыта - потерянный id не должен снимать
    блокировку `critical`. Находка, покрытая тредом прошлого прогона, своего
    комментария не получала и сюда не попадает. Прогон без сводки ревью не
    публиковал - и находок без треда у него нет.
    """
    comments = tuple(comments)
    if not any(c.kind is CommentKind.SUMMARY for c in comments):
        return ()
    tracked = {c.finding_id for c in comments if c.kind is CommentKind.INLINE}
    return tuple(
        f for f in findings if f.covered_by_finding_id is None and f.id not in tracked
    )
