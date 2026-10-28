"""Входы песочницы контура: файл с диапазоном строк и кейс YAML с ключом `lines:`.

Дифф строит фейковый GitHub (`difflib`) из базы и head. Чтобы дифф затронул
ровно заданные строки, база - это тот же файл, где эти строки заменены
уникальными заглушками `<песочница: строка N>`: если их просто убрать, `difflib`
сдвигает вставку на соседние одинаковые строки (пустые, `}`), и затронутым
оказывается не тот диапазон. Цена - hunk несёт и удалённые заглушки.
"""

import dataclasses
import re
from pathlib import Path

import yaml

from ..inputs import InputError, PrInput, load_case_file

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
        if not 1 <= start <= end <= total:
            raise InputError(f"строки «{part.strip()}» вне файла: в нём строк {total}")
        lines.update(range(start, end + 1))
    return frozenset(lines)


def with_touched(head: str, lines: frozenset[int]) -> str:
    """База файла: строки из `lines` заменены уникальными заглушками."""
    return "".join(
        f"<песочница: строка {n}>\n" if n in lines else line
        for n, line in enumerate(head.splitlines(keepends=True), start=1)
    )


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
    """Путь относительно текущего каталога, если файл внутри него, иначе как передан."""
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def from_file(path: Path, lines: str | None) -> PrInput:
    """PR из одного файла. Без `lines` файл новый и затронут целиком."""
    if not path.is_file():
        raise InputError(f"файла {path} нет")
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        raise InputError(f"файл {path} не в UTF-8") from error
    rel = _relative(path)
    base: dict[str, str] = {}
    if lines is not None:
        try:
            base[rel] = with_touched(text, parse_lines(lines, _line_count(text)))
        except InputError as error:
            raise InputError(f"файл {path}: {error}") from error
    return PrInput(
        id=re.sub(r"[^A-Za-z0-9._-]+", "_", rel).strip("_") or "file",
        title=f"file {rel}",
        pr_title=f"file {rel}",
        pr_description="",
        base_files=base,
        head_files={rel: text},
        expected=(),
    )


def load_outline_case(path: Path) -> PrInput:
    """Кейс в формате корпуса и необязательный ключ `lines:` (путь → строки).

    Файлы из `lines:` получают базу с заглушками, остальные остаются новыми.
    Для `make sandbox CASE_FILE=…` ключ безвреден: `load_case_file` его не читает.
    """
    pr = load_case_file(path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    spec = raw.get("lines")
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise InputError(f"кейс {path}: lines: должен быть словарём «путь: строки»")
    base: dict[str, str] = {}
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
        except InputError as error:
            raise InputError(f"кейс {path}: {file_path}: {error}") from error
        base[str(file_path)] = with_touched(head, touched)
    return dataclasses.replace(pr, base_files=base)
