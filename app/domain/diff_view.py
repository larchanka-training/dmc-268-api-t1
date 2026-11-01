"""Дифф прогона в том виде, в каком его показывает кабинет.

Чистая функция над разобранным `parse_diff` диффом: строки ханка выводятся из
его сырого `Hunk.text`, поэтому второй парсер формата не нужен, а сборка
контекста и привязка якорей работают с прежним `Hunk`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.code_context import language_for_path
from app.domain.diff_parser import number_lines, split_lines
from app.domain.entities import Hunk, ParsedFile
from app.domain.enums import DiffFileStatus, DiffLineKind, Language

# Текст заголовка после второго `@@` — обычно сигнатура окружающего кода.
_HEADER_TAIL = re.compile(r"^@@ [^@]* @@ ?(.*)$")


@dataclass(frozen=True, slots=True)
class DiffLine:
    """Строка ханка: `content` — без префикса `+`, `-` или пробела.

    `old_line` — `None` у добавленной строки, `new_line` — у удалённой.
    """

    kind: DiffLineKind
    old_line: int | None
    new_line: int | None
    content: str


@dataclass(frozen=True, slots=True)
class DiffHunkView:
    """Ханк: старты и счётчики — из заголовка `@@`, строки — из тела."""

    header: str
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[DiffLine, ...]


@dataclass(frozen=True, slots=True)
class DiffFileView:
    """Изменённый файл диффа прогона.

    `previous_path` — только у переименования. У бинарного файла ханков нет,
    счётчики нулевые. `language` — `None`, если грамматики для файла нет.
    """

    path: str
    previous_path: str | None
    status: DiffFileStatus
    is_binary: bool
    language: Language | None
    additions: int
    deletions: int
    hunks: tuple[DiffHunkView, ...]


def build_diff_view(files: Sequence[ParsedFile]) -> tuple[DiffFileView, ...]:
    """Представление всех файлов диффа в исходном порядке, без фильтрации.

    Lock-файлы, минифицированное и сгенерированное, которые ревью отсеивает
    (`filter_diff_files`), здесь остаются: кабинет показывает дифф целиком.
    """
    return tuple(_file_view(f) for f in files)


def _file_view(file: ParsedFile) -> DiffFileView:
    hunks = tuple(_hunk_view(h) for h in file.hunks)
    kinds = [line.kind for hunk in hunks for line in hunk.lines]
    status = _status(file)
    return DiffFileView(
        path=file.file_path,
        previous_path=file.old_path if status is DiffFileStatus.RENAMED else None,
        status=status,
        is_binary=file.is_binary,
        language=language_for_path(file.file_path),
        additions=kinds.count(DiffLineKind.ADDED),
        deletions=kinds.count(DiffLineKind.REMOVED),
        hunks=hunks,
    )


def _status(file: ParsedFile) -> DiffFileStatus:
    """Удаление и добавление важнее переименования: у них `old_path` нет."""
    if file.is_deleted:
        return DiffFileStatus.DELETED
    if file.is_added:
        return DiffFileStatus.ADDED
    if file.old_path is not None:
        return DiffFileStatus.RENAMED
    return DiffFileStatus.MODIFIED


def _hunk_view(hunk: Hunk) -> DiffHunkView:
    """Разложить ханк на строки той же нумерацией, что у парсера (`number_lines`)."""
    header_line, *body = split_lines(hunk.text)
    match = _HEADER_TAIL.match(header_line)
    lines = tuple(
        DiffLine(kind=kind, old_line=old_line, new_line=new_line, content=content)
        for kind, old_line, new_line, content in number_lines(body, hunk.old_start, hunk.new_start)
    )
    return DiffHunkView(
        header=match.group(1) if match is not None else "",
        old_start=hunk.old_start,
        old_count=hunk.old_count,
        new_start=hunk.new_start,
        new_count=hunk.new_count,
        lines=lines,
    )
