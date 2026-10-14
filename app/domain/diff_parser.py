"""Разбор unified diff на hunk'и для `validate_anchor`."""

import re

from app.domain.entities import Hunk

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff(raw: str) -> tuple[Hunk, ...]:
    """Вернуть hunk'и с множествами изменённых номеров строк (без контекста)."""
    if not raw.strip():
        return ()
    hunks: list[Hunk] = []
    current_path: str | None = None
    old_line = 0
    new_line = 0
    changed_old: set[int] = set()
    changed_new: set[int] = set()
    hunk_old_start = 0
    hunk_new_start = 0

    def flush_hunk() -> None:
        nonlocal changed_old, changed_new, hunk_old_start, hunk_new_start
        if current_path is None or not (changed_old or changed_new):
            changed_old = set()
            changed_new = set()
            return
        hunks.append(
            Hunk(
                file_path=current_path,
                old_start=hunk_old_start,
                new_start=hunk_new_start,
                changed_old_lines=frozenset(changed_old),
                changed_new_lines=frozenset(changed_new),
            )
        )
        changed_old = set()
        changed_new = set()

    for line in raw.splitlines():
        if line.startswith("diff --git"):
            flush_hunk()
            current_path = None
            continue
        if line.startswith("+++ b/"):
            flush_hunk()
            current_path = line[6:].split("\t", 1)[0]
            continue
        if line.startswith("--- a/"):
            continue
        header = _HUNK_HEADER.match(line)
        if header:
            flush_hunk()
            hunk_old_start = int(header.group(1))
            hunk_new_start = int(header.group(3))
            old_line = hunk_old_start
            new_line = hunk_new_start
            continue
        if current_path is None or not line:
            continue
        if line.startswith("\\"):
            continue
        kind = line[0]
        text = line[1:]
        if not text and kind not in "+-":
            continue
        if kind == "+":
            changed_new.add(new_line)
            new_line += 1
        elif kind == "-":
            changed_old.add(old_line)
            old_line += 1
        elif kind == " ":
            old_line += 1
            new_line += 1
    flush_hunk()
    return tuple(hunks)
