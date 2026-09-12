"""Collapsing findings that say the same thing twice.

The database enforces this too, with a unique constraint, so a duplicate that
slips past here still cannot be stored. Doing it in the domain first means the
caller learns what was dropped instead of catching an integrity error.
"""

from collections.abc import Iterable

from app.domain.entities import Finding

type _Identity = tuple[str, str, int | None, int | None, str]


def _identity(finding: Finding) -> _Identity:
    """The whole anchor, not just one of its two line numbers.

    Only the line number belonging to the anchor's side is ever populated, so
    keying on `new_line` alone would give every old-side finding in a file the
    same key and collapse unrelated ones into the first.
    """
    anchor = finding.anchor
    return (
        anchor.file_path,
        str(anchor.side),
        anchor.old_line,
        anchor.new_line,
        str(finding.category),
    )


def deduplicate(findings: Iterable[Finding]) -> tuple[list[Finding], list[Finding]]:
    """Split findings into the ones to keep and the ones already covered.

    Two findings collide when they point at the same file, the same side and
    coordinates of the diff, and share a category. The first one wins; order is
    preserved.
    """
    seen: set[_Identity] = set()
    kept: list[Finding] = []
    dropped: list[Finding] = []
    for finding in findings:
        key = _identity(finding)
        if key in seen:
            dropped.append(finding)
        else:
            seen.add(key)
            kept.append(finding)
    return kept, dropped
