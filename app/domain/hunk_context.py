"""Расширенные виды hunk'ов: окно вокруг изменения и файл целиком (уровни `surrounding`, `whole_file`).

Вид - тот же unified diff, что `git diff -U<радиус>`: к hunk'у добавляются строки
текста файла на `head_sha` с обеих сторон (они одинаковы в старой и новой
версии), hunk'и с пересекающимися или смыкающимися окнами склеиваются, заголовок
`@@` пересчитывается. Файл целиком - тот же вид с радиусом не меньше длины файла.
Исходный `Hunk` не меняется: по нему сверяются якоря находок.

Секреты в контекстных строках вида заменяются меткой: эти строки PR не менял, а
вид уходит внешней модели. Строки `+`/`-` остаются как есть - секрет, который PR
добавил или удалил, модель должна увидеть и назвать.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import groupby

from app.domain.diff_parser import split_lines
from app.domain.entities import Hunk
from app.domain.secret_redaction import redact_secrets

# Значения из §5 docs/SYSTEM_DESIGN.md; общий размер держит `context_max_tokens`.
SURROUNDING_LINES = 30
WHOLE_FILE_MAX_BYTES = 8 * 1024

_NO_NEWLINE = "\\ No newline at end of file"


def expand_hunks(hunks: Sequence[Hunk], source: str, radius: int) -> tuple[str, ...] | None:
    """Тексты расширенных видов hunk'ов одного файла по его тексту `source` на `head_sha`.

    Если контекстная или добавленная строка hunk'а не совпала со строкой
    `source`, hunk выходит за конец файла или в нём нет строк `+`/`-` - `None`:
    окно из другой версии файла хуже трёх строк контекста.
    """
    lines = split_lines(source) if source else []
    bodies = [h.text.split("\n")[1:] for h in hunks]
    if not all(
        _matches(h, body, lines) and any(line.startswith(("+", "-")) for line in body)
        for h, body in zip(hunks, bodies, strict=True)
    ):
        return None

    # Окно отмеряется от изменённых строк, как у git: свой контекст hunk'а снимается.
    cores = [_core(h, body) for h, body in zip(hunks, bodies, strict=True)]
    # Группы, чьи окна смыкаются: между изменениями не больше двух радиусов строк, как у git.
    groups: list[list[_Core]] = []
    for core in cores:
        if groups and core.new_first - groups[-1][-1].new_end <= 2 * radius:
            groups[-1].append(core)
        else:
            groups.append([core])

    views = []
    for group in groups:
        first, last = group[0], group[-1]
        before = min(radius, first.new_first - 1)
        after = min(radius, len(lines) - last.new_end + 1)
        body = _context(lines, first.new_first - before, first.new_first)
        old_count = new_count = before + after
        for previous, core in zip([None, *group], group, strict=False):
            if previous is not None:
                body += _context(lines, previous.new_end, core.new_first)
                old_count += core.new_first - previous.new_end
                new_count += core.new_first - previous.new_end
            body += core.body
            old_count += core.old_count
            new_count += core.new_count
        body += _context(lines, last.new_end, last.new_end + after)
        if after and last.new_end + after - 1 == len(lines) and not source.endswith("\n"):
            body.append(_NO_NEWLINE)
        header = (
            f"@@ -{_range(first.old_first - before, old_count)}"
            f" +{_range(first.new_first - before, new_count)} @@"
        )
        views.append("\n".join([header, *_redact_context(body)]))
    return tuple(views)


@dataclass(frozen=True, slots=True)
class _Core:
    """Hunk без своего ведущего и хвостового контекста: от первого изменения до последнего.

    `*_first` - номер первой строки стороны (у пустой стороны - следующей за местом
    изменения), `new_end` - номер первой строки новой стороны после него.
    """

    old_first: int
    new_first: int
    old_count: int
    new_count: int
    body: list[str]

    @property
    def new_end(self) -> int:
        return self.new_first + self.new_count


def _core(hunk: Hunk, body: list[str]) -> _Core:
    lead = next(i for i, line in enumerate(body) if line.startswith(("+", "-")))
    end = len(body)
    # Хвост: контекстные строки и маркер `\` после контекстной строки; маркер после `+`/`-` - часть изменения.
    while body[end - 1].startswith(" ") or (body[end - 1].startswith("\\") and body[end - 2].startswith(" ")):
        end -= 1
    trail = sum(1 for line in body[end:] if line.startswith(" "))
    old_first = hunk.old_start if hunk.old_count else hunk.old_start + 1
    new_first = hunk.new_start if hunk.new_count else hunk.new_start + 1
    return _Core(
        old_first=old_first + lead,
        new_first=new_first + lead,
        old_count=hunk.old_count - lead - trail,
        new_count=hunk.new_count - lead - trail,
        body=body[lead:end],
    )


def _redact_context(body: list[str]) -> list[str]:
    """Секреты в контекстных строках - блоками подряд идущих строк, чтобы ключ PEM на
    нескольких строках узнавался целиком. `redact_secrets` ставит метку на каждую
    съеденную строку, поэтому строки вида остаются на своих местах.
    """
    out: list[str] = []
    for is_context, run in groupby(body, key=lambda line: line.startswith(" ")):
        lines = list(run)
        # С префиксом-пробелом: так `redact_secrets` читает блок как строки диффа и
        # оставляет пробел у каждой строки метки (у строки `-----END` тоже).
        out += redact_secrets("\n".join(lines)).split("\n") if is_context else lines
    return out


def _context(lines: Sequence[str], first: int, end: int) -> list[str]:
    """Контекстные строки файла с номерами `first`..`end - 1`."""
    return [f" {line}" for line in lines[first - 1 : end - 1]]


def _range(first: int, count: int) -> str:
    """Диапазон заголовка, как пишет git: у пустого - строка перед ним, счётчик 1 опускается."""
    if count == 0:
        return f"{first - 1},0"
    return str(first) if count == 1 else f"{first},{count}"


def _matches(hunk: Hunk, body: Sequence[str], lines: Sequence[str]) -> bool:
    """Контекстные и добавленные строки hunk'а совпадают со строками файла по номерам."""
    number = hunk.new_start if hunk.new_count else hunk.new_start + 1
    if number + hunk.new_count - 1 > len(lines):
        return False
    for line in body:
        if line.startswith(("-", "\\")):
            continue
        if number > len(lines) or lines[number - 1] != line[1:]:
            return False
        number += 1
    return True
