"""Сопоставление пути с glob-шаблоном без экспоненциального перебора.

Шаблоны пишет владелец репозитория в `.review/rules.md`, имена файлов - автор
PR. `PurePosixPath.full_match` переводит шаблон в регулярное выражение, и
шаблон вида `*a*a*a…*x` на длинном имени перебирает варианты экспоненциально
долго, не отпуская GIL: воркер зависает, и таймаут его не прерывает (#222).

Здесь та же семантика, что у `full_match` для POSIX-пути: сегменты через `/`,
сегмент `**` - ноль и больше сегментов, а последним в шаблоне - один и больше
(`app/**` не совпадает с самим `app`). Внутри сегмента - `fnmatchcase`: `*`,
`?`, `[…]` и `[!…]`, регистр учитывается. Со слешем подстановки не совпадают:
в имени сегмента его нет. Сегменты сопоставляются жадно с откатом только к
последнему `**` - время не больше произведения длин; `fnmatchcase` внутри
сегмента с 3.14 тоже без экспоненциального перебора (атомарные группы).
"""

from fnmatch import fnmatchcase

_GLOBSTAR = "**"


def glob_full_match(path: str, pattern: str) -> bool:
    """Путь целиком совпадает с шаблоном - как `PurePosixPath(path).full_match(pattern)`.

    Жадное сопоставление: при неудаче `**`, встреченный последним, забирает на
    сегмент больше. Откат только к нему даёт O(сегменты пути * сегменты шаблона) -
    более ранним `**` переигрывать нечего.
    """
    names = _segments(path)
    # `None` - любой один сегмент: так хвостовой `**` требует хотя бы один.
    parts: list[str | None] = list(_segments(pattern))
    if parts and parts[-1] == _GLOBSTAR:
        parts.insert(len(parts) - 1, None)
    i = j = 0
    star_j = -1
    star_i = 0
    while i < len(names):
        if j < len(parts) and parts[j] == _GLOBSTAR:
            star_j, star_i = j, i
            j += 1
        elif j < len(parts) and _accepts(parts[j], names[i]):
            i += 1
            j += 1
        elif star_j >= 0:
            star_i += 1
            i, j = star_i, star_j + 1
        else:
            return False
    while j < len(parts) and parts[j] == _GLOBSTAR:
        j += 1
    return j == len(parts)


def _accepts(part: str | None, name: str) -> bool:
    # `None` - любой один сегмент.
    return part is None or fnmatchcase(name, part)


def _segments(text: str) -> list[str]:
    # Как у PurePosixPath: пустые сегменты и `.` схлопываются.
    return [s for s in text.split("/") if s not in ("", ".")]
