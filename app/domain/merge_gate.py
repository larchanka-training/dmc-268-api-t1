"""Решение о блокировке мерджа по открытым критичным замечаниям."""

from app.domain.entities import Finding
from app.domain.enums import FindingSeverity

BLOCKING_SEVERITIES: frozenset[FindingSeverity] = frozenset({FindingSeverity.CRITICAL})


def merge_blocked_by_findings(
    open_findings: tuple[Finding, ...],
    *,
    blocking: frozenset[FindingSeverity] = BLOCKING_SEVERITIES,
) -> bool:
    return any(f.severity in blocking for f in open_findings)
