"""Привязка замечания к строке, которую изменение действительно затронуло.

Модель с радостью сообщит о проблеме в строке, которой нет в диффе. Правило,
которое такие отбрасывает, живёт здесь, получает разобранные hunk'и
аргументом и не читает файлы, поэтому тестируется на литеральных данных.
"""

from collections.abc import Iterable

from app.domain.entities import DiffAnchor, Hunk
from app.domain.enums import DiffSide
from app.domain.result import Result


def validate_anchor(anchor: DiffAnchor, hunks: Iterable[Hunk]) -> Result[DiffAnchor]:
    """Убедиться, что привязка указывает на строку внутри изменения."""
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
