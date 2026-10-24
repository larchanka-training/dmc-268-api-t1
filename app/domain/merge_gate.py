"""Решение о блокировке мерджа по открытым критичным замечаниям."""

from app.domain.entities import Finding
from app.domain.enums import FindingSeverity


def merge_blocked_by_findings(open_findings: tuple[Finding, ...]) -> bool:
    return any(f.severity is FindingSeverity.CRITICAL for f in open_findings)
