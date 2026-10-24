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


def split_lines(text: str) -> list[str]:
    """Разбить текст на строки только по `\n`, как git и GitHub.

    `str.splitlines()` режет и по `\x0c`, `\x85`, `\u2028`…: хвост строки кода
    становится фальшивым заголовком файла, hunk'а или раздела правил, а номера
    строк уезжают. `\r` перед `\n` отбрасывается, как отбрасывала `splitlines()`.
    """
    return [line.removesuffix("\r") for line in text.removesuffix("\n").split("\n")]


def parse_diff(raw: str) -> list[ParsedFile]:
    """Разбить unified diff на файлы. Пустому и пробельному входу — []."""
    if not raw.strip():
        return []
    chunks: list[list[str]] = []
    for line in split_lines(raw):
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
    is_deleted = False
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
            is_deleted = plus_path is None
        elif line.startswith("deleted file mode"):
            is_deleted = True
        elif line.startswith("rename from "):
            rename_from = _unquote(line[len("rename from ") :])
        elif line.startswith("rename to "):
            rename_to = _unquote(line[len("rename to ") :])
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
        is_deleted=is_deleted,
        hunks=tuple(hunks),
    )


def _build_hunk(file_path: str, header: re.Match[str], body: list[str]) -> Hunk:
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
        text="\n".join([header.string, *body]),
    )


def _path_after_marker(line: str, marker: str) -> str | None:
    """Путь из `--- a/x` / `+++ b/x`; `/dev/null` означает «стороны нет»."""
    raw = line[len(marker) :]
    if raw.startswith('"'):
        value = _read_quoted(raw)[0]
    else:
        value = raw.split("\t")[0].strip()
        if value == "/dev/null":
            return None
    return _strip_git_prefix(value)


def _paths_from_git_header(line: str) -> tuple[str, str]:
    """Обе стороны из `diff --git a/x b/y` — запас, когда нет `---`/`+++`."""
    rest = line[len("diff --git ") :]
    if rest.startswith('"'):
        a_raw, tail = _read_quoted(rest)
        tail = tail.lstrip(" ")
        b_raw = _read_quoted(tail)[0] if tail.startswith('"') else tail
        return _strip_git_prefix(a_raw), _strip_git_prefix(b_raw)
    if ' "b/' in rest:
        a_raw, b_quoted = rest.split(' "', 1)
        return _strip_git_prefix(a_raw), _strip_git_prefix(_read_quoted('"' + b_quoted)[0])
    if " b/" in rest:
        a_raw, b_raw = rest.split(" b/", 1)
        return _strip_git_prefix(a_raw), b_raw
    stripped = _strip_git_prefix(rest)
    return stripped, stripped


def _strip_git_prefix(path: str) -> str:
    return path[2:] if path.startswith(("a/", "b/")) else path


# C-quoting git (`core.quotePath`, по умолчанию включён): путь с не-ASCII,
# управляющими символами, `"` или `\` идёт в кавычках, байты UTF-8 —
# восьмеричными escape (`"b/\320\277.py"`).
_C_ESCAPES = {"a": "\a", "b": "\b", "t": "\t", "n": "\n", "v": "\v", "f": "\f", "r": "\r"}
# Байт — не больше \377: иное git не выдаёт, и `bytearray` его не примет.
_OCTAL = re.compile(r"[0-3][0-7]{2}")


def _unquote(value: str) -> str:
    """Путь как есть или раскрытый из кавычек git."""
    return _read_quoted(value)[0] if value.startswith('"') else value


def _read_quoted(text: str) -> tuple[str, str]:
    """Раскрыть путь в кавычках в начале `text`; вернуть путь и остаток строки.

    Восьмеричные escape — байты, поэтому путь собирается в байтах и
    декодируется как UTF-8 целиком. Без закрывающей кавычки строка не
    раскрывается: это не C-quoting git, а путь, начинающийся с `"`.
    """
    out = bytearray()
    i = 1
    while i < len(text):
        char = text[i]
        if char == '"':
            return out.decode("utf-8", errors="replace"), text[i + 1 :]
        if char == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if _OCTAL.match(text, i + 1):
                out.append(int(text[i + 1 : i + 4], 8))
                i += 4
                continue
            out += _C_ESCAPES.get(nxt, nxt).encode("utf-8")
            i += 2
            continue
        out += char.encode("utf-8")
        i += 1
    return text, ""

