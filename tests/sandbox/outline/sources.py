"""Входы песочницы контура: файл с диапазоном строк и кейс YAML с ключом `lines:`.

База файла - тот же файл, где заданные строки заменены заглушками
`<песочница: строка N>`. Дифф такого файла строится не `difflib` по тексту, а по
номерам строк (`touched_diff`): `difflib` сопоставляет одинаковые строки с самым
ранним вхождением, и неизменённый хвост или промежуток между диапазонами
съезжает на такую же строку внутри диапазона - затронутым оказывается не тот
диапазон. Цена - hunk несёт и удалённые заглушки.
"""

import difflib
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.domain.code_context import touched_lines
from app.domain.diff_parser import parse_diff

from ...fakes.github_api import file_diff
from ..inputs import InputError, PrInput, load_case_file


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


def with_touched(head: str, lines: frozenset[int]) -> str:
    """База файла: строки из `lines` заменены уникальными заглушками."""
    return "".join(
        f"<песочница: строка {n}>\n" if n in lines else line
        for n, line in enumerate(head.splitlines(keepends=True), start=1)
    )


def _hunk_range(start: int, stop: int) -> str:
    """Диапазон стороны hunk'а, как у `difflib.unified_diff`."""
    length = stop - start
    if length == 1:
        return str(start + 1)
    return f"{start if not length else start + 1},{length}"


def touched_diff(path: str, head: str, lines: frozenset[int]) -> str:
    """Дифф файла, затрагивающий ровно `lines`: база - `with_touched(head, lines)`.

    Строки сопоставляются по номеру, а не по тексту: неизменённая строка `n`
    базы - это строка `n` head, и никакая другая. Если разбор диффа всё же даст
    другие строки - `InputError`, а не чужой контекст в отчёте.
    """
    base = with_touched(head, lines).splitlines(keepends=True)
    new = head.splitlines(keepends=True)

    def keys(tag: str) -> list[tuple[str, int]]:
        return [(tag if n in lines else "eq", n) for n in range(1, len(new) + 1)]

    matcher = difflib.SequenceMatcher(None, keys("ph"), keys("new"), autojunk=False)
    out = [f"diff --git a/{path} b/{path}\n", f"--- a/{path}\n", f"+++ b/{path}\n"]
    for group in matcher.get_grouped_opcodes(3):
        first, last = group[0], group[-1]
        out.append(
            f"@@ -{_hunk_range(first[1], last[2])} +{_hunk_range(first[3], last[4])} @@\n"
        )
        for tag, i1, i2, j1, j2 in group:
            if tag == "equal":
                out += (" " + line for line in base[i1:i2])
                continue
            out += ("-" + line for line in base[i1:i2])
            out += ("+" + line for line in new[j1:j2])
    diff = "".join(line if line.endswith("\n") else line + "\n" for line in out)
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
            base[rel] = with_touched(text, touched)
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

    Файлы из `lines:` получают базу с заглушками, остальные остаются новыми.
    Для `make sandbox CASE_FILE=…` ключ безвреден: `load_case_file` его не читает.
    """
    try:
        pr = load_case_file(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise InputError(f"кейс {path} не прочитать: {error.strerror}") from error
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
        base[str(file_path)] = with_touched(head, touched)
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
