"""Разбор unified diff на файлы и фильтрация тех, что не стоит ревьюить.

GitHub отдаёт дифф пул-реквеста текстом; чтобы отличить шум от предмета
ревью, дифф разбирается здесь — один раз, без сети и базы. Функции чистые:
дифф приходит строкой, уходит список `ParsedFile`, поэтому всё тестируется
на литеральных диффах.

Паттерны фильтрации объявлены константами этого модуля: домен — единственный
источник, адаптеры его не дублируют.
"""

import re
from collections.abc import Iterable

from app.domain.entities import Hunk, ParsedFile

LOCK_FILE_NAMES: frozenset[str] = frozenset(
    {
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "composer.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "go.sum",
    }
)
MINIFIED_SUFFIXES: tuple[str, ...] = (".min.js", ".min.css")
GENERATED_MARKER = ".generated."
PROTOBUF_SUFFIX = ".pb.go"
VENDOR_DIRECTORIES: frozenset[str] = frozenset({"vendor", "node_modules"})

_HUNK_HEADER = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_diff(raw: str) -> list[ParsedFile]:
    """Разбить unified diff на файлы. Пустому и пробельному входу — []."""
    if not raw.strip():
        return []
    chunks: list[list[str]] = []
    for line in raw.splitlines():
        if line.startswith("diff --git "):
            chunks.append([line])
        elif chunks:
            chunks[-1].append(line)
    return [_parse_file(chunk) for chunk in chunks]


def is_filterable(file_path: str, is_binary: bool) -> bool:
    """Правда, если файл — шум для ревью и модели его отправлять не стоит."""
    if is_binary:
        return True
    parts = file_path.split("/")
    name = parts[-1]
    return (
        name in LOCK_FILE_NAMES
        or name.endswith(MINIFIED_SUFFIXES)
        or GENERATED_MARKER in name
        or name.endswith(PROTOBUF_SUFFIX)
        or any(part in VENDOR_DIRECTORIES for part in parts[:-1])
    )


def filter_diff_files(files: Iterable[ParsedFile]) -> list[ParsedFile]:
    """Оставить файлы, подлежащие ревью, в исходном порядке."""
    return [f for f in files if not is_filterable(f.file_path, f.is_binary)]


def _parse_file(lines: list[str]) -> ParsedFile:
    """Собрать один файл из его куска диффа: от `diff --git` до конца файла."""
    a_path, b_path = _paths_from_git_header(lines[0])
    minus_path: str | None = None
    plus_path: str | None = None
    rename_from: str | None = None
    rename_to: str | None = None
    is_binary = False
    hunks: list[Hunk] = []
    header: re.Match[str] | None = None
    body: list[str] = []
    file_path = ""

    for line in lines[1:]:
        if (match := _HUNK_HEADER.match(line)) is not None:
            if header is not None:
                hunks.append(_build_hunk(file_path, header, body))
            # Все маркеры путей стоят до первого hunk'а, поэтому путь уже известен.
            file_path = plus_path or rename_to or b_path or minus_path or a_path
            header, body = match, []
        elif header is not None:
            body.append(line)
        elif line.startswith("--- "):
            minus_path = _path_after_marker(line, "--- ")
        elif line.startswith("+++ "):
            plus_path = _path_after_marker(line, "+++ ")
        elif line.startswith("rename from "):
            rename_from = line[len("rename from ") :]
        elif line.startswith("rename to "):
            rename_to = line[len("rename to ") :]
        elif line.startswith("Binary files ") or line == "GIT binary patch":
            is_binary = True
    if header is not None:
        hunks.append(_build_hunk(file_path, header, body))

    old_side = minus_path or rename_from or a_path
    new_side = plus_path or rename_to or b_path
    is_rename = rename_from is not None or new_side != old_side
    return ParsedFile(
        file_path=file_path or new_side or old_side or "",
        old_path=old_side if is_rename else None,
        is_binary=is_binary,
        hunks=tuple(hunks),
    )


def _build_hunk(
    file_path: str, header: re.Match[str], body: Iterable[str]
) -> Hunk:
    """Собрать hunk: старты и счётчики — из заголовка, номера строк — из тела.

    Опущенный в заголовке счётчик означает одну строку (`@@ -5 +5 @@`).
    Строка `\\ No newline at end of file` — не строка файла: счётчики она не
    сдвигает.
    """
    old_start = int(header.group(1))
    old_count = int(header.group(2) or "1")
    new_start = int(header.group(3))
    new_count = int(header.group(4) or "1")
    old_line, new_line = old_start, new_start
    changed_old: set[int] = set()
    changed_new: set[int] = set()
    for line in body:
        if line.startswith("+"):
            changed_new.add(new_line)
            new_line += 1
        elif line.startswith("-"):
            changed_old.add(old_line)
            old_line += 1
        elif line.startswith("\\"):
            continue
        else:
            old_line += 1
            new_line += 1
    return Hunk(
        file_path=file_path,
        old_start=old_start,
        old_count=old_count,
        new_start=new_start,
        new_count=new_count,
        changed_new_lines=frozenset(changed_new),
        changed_old_lines=frozenset(changed_old),
    )


def _path_after_marker(line: str, marker: str) -> str | None:
    """Путь из `--- a/x` / `+++ b/x`; `/dev/null` означает «стороны нет»."""
    value = line[len(marker) :].split("\t")[0].strip()
    if value == "/dev/null":
        return None
    if value.startswith(("a/", "b/")):
        value = value[2:]
    return value


def _paths_from_git_header(line: str) -> tuple[str, str]:
    """Обе стороны из `diff --git a/x b/y` — запас, когда нет `---`/`+++`."""
    rest = line[len("diff --git ") :]
    if " b/" in rest:
        a_raw, b_raw = rest.split(" b/", 1)
        return _strip_git_prefix(a_raw), b_raw
    stripped = _strip_git_prefix(rest)
    return stripped, stripped


def _strip_git_prefix(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path
