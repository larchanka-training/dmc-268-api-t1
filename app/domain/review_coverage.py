"""Охват ревью: какие файлы диффа модель видела целиком, частично или не видела (#279).

Считается кодом по тому, что вошло в пакет контекста, — модель об охвате не спрашивают:
она не знает, что сборка отложила.
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from app.domain.diff_parser import is_filtered_out
from app.domain.entities import Hunk, ParsedFile


@dataclass(frozen=True)
class ReviewCoverage:
    reviewed: tuple[str, ...]
    # Часть hunk'ов файла не вошла в лимит контекста.
    partial: tuple[str, ...]
    # Ни один hunk файла не вошёл в лимит контекста.
    not_reviewed: tuple[str, ...]
    # Lock-файлы, сгенерированные, бандлы, vendor, бинарные и изменения больше лимита
    # (`is_filtered_out`) — то же правило, что у `filter_diff_files`.
    filtered: tuple[str, ...]


def review_coverage(
    files: Sequence[ParsedFile], shown: Iterable[Hunk], *, max_file_bytes: int
) -> ReviewCoverage:
    """Файлы диффа по группам охвата; `shown` — hunk'и, вошедшие в пакет."""
    shown_per_file = Counter(h.file_path for h in shown)
    reviewed: list[str] = []
    partial: list[str] = []
    not_reviewed: list[str] = []
    filtered: list[str] = []
    for file in files:
        seen = shown_per_file[file.file_path]
        if is_filtered_out(file, max_file_bytes):
            filtered.append(file.file_path)
        elif not file.hunks:
            # Переименование без правок или смена режима: смотреть нечего, в списке его нет.
            continue
        elif seen >= len(file.hunks):
            reviewed.append(file.file_path)
        elif seen:
            partial.append(file.file_path)
        else:
            not_reviewed.append(file.file_path)
    return ReviewCoverage(tuple(reviewed), tuple(partial), tuple(not_reviewed), tuple(filtered))
