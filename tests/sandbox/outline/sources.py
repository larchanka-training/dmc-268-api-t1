"""Входы песочницы контура: файл с диапазоном строк и кейс YAML с ключом `lines:`.

Заданные строки - добавленные: база файла - тот же файл без них. Дифф строится не
`difflib` по тексту, а сразу по номерам строк (`touched_diff`): `difflib`
сопоставляет одинаковые строки с самым ранним вхождением, и неизменённый хвост
или промежуток между диапазонами съезжает на такую же строку внутри диапазона -
затронутым оказывается не тот диапазон.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from app.domain.code_context import touched_lines
from app.domain.diff_parser import parse_diff

from ...fakes.github_api import file_diff
from ..inputs import InputError, PrInput, case_input, read_case_yaml


@dataclass(frozen=True, slots=True)
class OutlinePr(PrInput):
    """PR песочницы контура: `diffs` - готовые диффы файлов с заданными строками.

    Остальные файлы фейковый GitHub диффует сам (`file_diff`).
    """

    diffs: Mapping[str, str] = field(default_factory=dict)

    def pull_diff(self) -> str:
        """Дифф всего PR в том порядке, в каком его строит `FakeGitHub.open_pull`."""
        return "".join(
            self.diffs.get(path) or file_diff(path, self.base_files.get(path), head)
            for path, head in self.head_files.items()
        )

_RANGE = re.compile(r"^(\d+)(?:-(\d+))?$")


def parse_lines(spec: str, total: int) -> frozenset[int]:
    """Строки из `"N"`, `"N-M"` или списка через запятую; `1 ≤ N ≤ M ≤ total`."""
    lines: set[int] = set()
    for part in spec.split(","):
        match = _RANGE.match(part.strip())
        if match is None:
            raise InputError(
                f"строки «{spec}» не в форме N, N-M или списка через запятую"
            )
        start = int(match[1])
        end = int(match[2] or start)
        if start > end:
            raise InputError(f"строки «{part.strip()}»: диапазон перевёрнут")
        if not 1 <= start <= end <= total:
            raise InputError(f"строки «{part.strip()}» вне файла: в нём строк {total}")
        lines.update(range(start, end + 1))
    return frozenset(lines)


# Строк контекста вокруг затронутых, как у `git diff` и фейкового GitHub.
CONTEXT_LINES = 3


def without_lines(head: str, lines: frozenset[int]) -> str:
    """База файла: `head` без строк из `lines`."""
    return "".join(
        line
        for n, line in enumerate(head.splitlines(keepends=True), start=1)
        if n not in lines
    )


def _side(start: int, count: int) -> str:
    """Сторона заголовка hunk'а; счётчик 1 опускается, как у `difflib`."""
    return str(start) if count == 1 else f"{start},{count}"


def touched_diff(path: str, head: str, lines: frozenset[int]) -> str:
    """Дифф файла, добавляющий ровно `lines` к базе `without_lines(head, lines)`.

    Hunk - затронутые строки (`+`) и по `CONTEXT_LINES` строк контекста вокруг;
    группы, чей контекст сходится, сливаются в один hunk. Если разбор диффа всё
    же даст другие строки - `InputError`, а не чужой контекст в отчёте.
    """
    # Концы строк как в `head` (`\r\n` тоже): окна и `whole_file` их сохраняют.
    new = head.splitlines(keepends=True)
    groups: list[list[int]] = []
    for n in sorted(lines):
        if groups and n - groups[-1][-1] - 1 <= 2 * CONTEXT_LINES:
            groups[-1].append(n)
        else:
            groups.append([n])
    out = [f"diff --git a/{path} b/{path}\n", f"--- a/{path}\n", f"+++ b/{path}\n"]
    added_before = 0
    for group in groups:
        first = max(1, group[0] - CONTEXT_LINES)
        last = min(len(new), group[-1] + CONTEXT_LINES)
        added = sum(1 for n in range(first, last + 1) if n in lines)
        old_count = last - first + 1 - added
        old_start = first - added_before - (0 if old_count else 1)
        out.append(
            f"@@ -{_side(old_start, old_count)} +{_side(first, last - first + 1)} @@\n"
        )
        out += (
            ("+" if n in lines else " ")
            + new[n - 1]
            + ("" if new[n - 1].endswith("\n") else "\n")
            for n in range(first, last + 1)
        )
        added_before += added
    diff = "".join(out)
    [parsed] = parse_diff(diff)
    got = touched_lines(parsed.hunks)
    if got != lines:
        raise InputError(
            f"дифф {path} затронул бы не те строки: {sorted(got ^ lines)[:5]}"
        )
    return diff


def _line_count(text: str) -> int:
    """Число строк для `--lines`/`lines:`; файл с разделителями кроме `\\n` - `InputError`.

    `file_diff` режет строки `str.splitlines()`: `\\f`, `\\v`, `\\x85`, одиночный
    `\\r` и прочие тоже разделители, и номера строк расходятся с редактором.
    """
    count = len(text.splitlines())
    if count != (len(text.removesuffix("\n").split("\n")) if text else 0):
        raise InputError(
            "разделители строк кроме \\n — номера строк разойдутся с редактором"
        )
    return count


def _relative(path: Path) -> str:
    """Путь относительно текущего каталога, если файл внутри него, иначе абсолютный.

    Относительный путь вне каталога (`../dir/m.py`) с `..` фейковый GitHub не найдёт.
    """
    resolved = path.resolve()
    try:
        return resolved.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def from_file(path: Path, lines: str | None) -> OutlinePr:
    """PR из одного файла. Без `lines` файл новый и затронут целиком."""
    if not path.is_file():
        raise InputError(f"файла {path} нет")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise InputError(f"файл {path} не в UTF-8") from error
    except OSError as error:
        raise InputError(f"файл {path} не прочитать: {error.strerror}") from error
    rel = _relative(path)
    base: dict[str, str] = {}
    diffs: dict[str, str] = {}
    if lines is not None:
        try:
            touched = parse_lines(lines, _line_count(text))
            base[rel] = without_lines(text, touched)
            diffs[rel] = touched_diff(rel, text, touched)
        except InputError as error:
            raise InputError(f"файл {path}: {error}") from error
    return OutlinePr(
        id=re.sub(r"[^A-Za-z0-9._-]+", "_", rel).strip("_") or "file",
        title=f"file {rel}",
        pr_title=f"file {rel}",
        pr_description="",
        base_files=base,
        head_files={rel: text},
        expected=(),
        diffs=diffs,
    )


def load_outline_case(path: Path) -> OutlinePr:
    """Кейс в формате корпуса и необязательный ключ `lines:` (путь → строки).

    YAML читается один раз. Файлы из `lines:` получают базу без этих строк,
    остальные остаются новыми. Для `make sandbox CASE_FILE=…` ключ безвреден:
    `load_case_file` его не читает.
    """
    raw = read_case_yaml(path)
    pr = case_input(raw, path)
    spec = raw.get("lines")
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise InputError(f"кейс {path}: lines: должен быть словарём «путь: строки»")
    base: dict[str, str] = {}
    diffs: dict[str, str] = {}
    for file_path, value in spec.items():
        head = pr.head_files.get(str(file_path))
        if head is None:
            raise InputError(
                f"кейс {path}: файла {file_path} из lines: нет среди files"
            )
        if not isinstance(value, str | int) or isinstance(value, bool):
            raise InputError(f"кейс {path}: строки {file_path} - не N, N-M или список")
        try:
            touched = parse_lines(str(value), _line_count(head))
            diffs[str(file_path)] = touched_diff(str(file_path), head, touched)
        except InputError as error:
            raise InputError(f"кейс {path}: {file_path}: {error}") from error
        base[str(file_path)] = without_lines(head, touched)
    return OutlinePr(
        id=pr.id,
        title=pr.title,
        pr_title=pr.pr_title,
        pr_description=pr.pr_description,
        base_files=base,
        head_files=pr.head_files,
        expected=pr.expected,
        diffs=diffs,
    )
