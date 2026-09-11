"""Anchoring a finding to a line the change actually touched.

A model will happily report a problem on a line that is not in the diff. The
rule that rejects those lives here, takes the parsed hunks as an argument, and
reads no files, so it is tested on literal data.
"""

from collections.abc import Iterable

from app.domain.entities import DiffAnchor, Hunk
from app.domain.enums import DiffSide
from app.domain.result import Result


def validate_anchor(anchor: DiffAnchor, hunks: Iterable[Hunk]) -> Result[DiffAnchor]:
    """Confirm the anchor points at a line inside the change."""
    for_file = [h for h in hunks if h.file_path == anchor.file_path]
    if not for_file:
        return Result.failure(f"{anchor.file_path} is not in the diff")

    if anchor.side is DiffSide.NEW:
        line, touched = anchor.new_line, "changed_new_lines"
    else:
        line, touched = anchor.old_line, "changed_old_lines"

    if line is None:
        return Result.failure(f"anchor on the {anchor.side} side carries no line number")

    if any(line in getattr(hunk, touched) for hunk in for_file):
        return Result.success(anchor)

    return Result.failure(
        f"{anchor.file_path}:{line} is outside the changed lines of the diff"
    )
