"""Сопоставление пути с glob-шаблоном без экспоненциального перебора.

Шаблоны пишет владелец репозитория в `.review/rules.md`, имена файлов - автор
PR. `PurePosixPath.full_match` переводит шаблон в регулярное выражение, и
шаблон вида `*a*a*a…*x` на длинном имени перебирает варианты экспоненциально
долго, не отпуская GIL: воркер зависает, и таймаут его не прерывает (#222).

Здесь та же семантика, что у `full_match` для POSIX-пути: сегменты через `/`,
сегмент `**` - ноль и больше сегментов, а последним в шаблоне - один и больше
(`app/**` не совпадает с самим `app`), `*` - любые символы внутри сегмента
(подряд идущие `*` внутри сегмента - одна `*`), `?` - один символ, `[…]` и
`[!…]` - класс символов с диапазонами, незакрытая `[` - буква. Регистр
учитывается. Время - не больше произведения длин: и сегменты, и символы
сопоставляются жадно с откатом только к последней звёздочке.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

_STAR = "*"
_GLOBSTAR = "**"
# Любой один сегмент: так хвостовой `**` требует хотя бы один сегмент.
_ANY_SEGMENT = "?"


@dataclass(frozen=True, slots=True)
class _CharClass:
    ranges: tuple[tuple[str, str], ...]
    negated: bool

    def accepts(self, char: str) -> bool:
        inside = any(low <= char <= high for low, high in self.ranges)
        return inside is not self.negated


# Токен сегмента: `_STAR`, `?` (любой символ), класс или буква - строка из одного символа.
type _Token = str | _CharClass


def glob_full_match(path: str, pattern: str) -> bool:
    """Путь целиком совпадает с шаблоном - как `PurePosixPath(path).full_match(pattern)`."""
    names = _segments(path)
    parts: list[str | tuple[_Token, ...]] = [
        _GLOBSTAR if segment == _GLOBSTAR else _tokens(segment) for segment in _segments(pattern)
    ]
    if parts and parts[-1] == _GLOBSTAR:
        parts.insert(len(parts) - 1, _ANY_SEGMENT)
    return _match(
        names,
        parts,
        is_star=lambda part: part == _GLOBSTAR,
        accepts=_part_accepts,
    )


def _part_accepts(part: str | tuple[_Token, ...], name: str) -> bool:
    if isinstance(part, str):
        return part == _ANY_SEGMENT
    return _segment_matches(name, part)


def _segments(text: str) -> list[str]:
    # Как у PurePosixPath: пустые сегменты и `.` схлопываются.
    return [s for s in text.split("/") if s not in ("", ".")]


def _segment_matches(name: str, tokens: tuple[_Token, ...]) -> bool:
    return _match(
        list(name),
        list(tokens),
        is_star=lambda token: token == _STAR,
        accepts=_token_accepts,
    )


def _token_accepts(token: _Token, char: str) -> bool:
    if isinstance(token, _CharClass):
        return token.accepts(char)
    return token == "?" or token == char


def _match[I, P](
    items: Sequence[I],
    pattern: Sequence[P],
    *,
    is_star: Callable[[P], bool],
    accepts: Callable[[P, I], bool],
) -> bool:
    """Жадное сопоставление со звёздочкой: при неудаче звёздочка, встреченная
    последней, забирает на один элемент больше. Откат только к ней даёт
    O(len(items) * len(pattern)) - более ранним звёздочкам переигрывать нечего."""
    i = j = 0
    star_j = -1
    star_i = 0
    while i < len(items):
        if j < len(pattern) and is_star(pattern[j]):
            star_j, star_i = j, i
            j += 1
        elif j < len(pattern) and accepts(pattern[j], items[i]):
            i += 1
            j += 1
        elif star_j >= 0:
            star_i += 1
            i, j = star_i, star_j + 1
        else:
            return False
    while j < len(pattern) and is_star(pattern[j]):
        j += 1
    return j == len(pattern)


def _tokens(segment: str) -> tuple[_Token, ...]:
    tokens: list[_Token] = []
    k = 0
    while k < len(segment):
        char = segment[k]
        if char == "*":
            if not tokens or tokens[-1] != _STAR:
                tokens.append(_STAR)
            k += 1
        elif char == "[":
            parsed = _char_class(segment, k)
            if parsed is None:
                tokens.append("[")
                k += 1
            else:
                char_class, k = parsed
                tokens.append(char_class)
        else:
            tokens.append(char)
            k += 1
    return tuple(tokens)


def _char_class(segment: str, start: int) -> tuple[_CharClass, int] | None:
    """Класс `[…]` с позиции `start`; `None` - нет закрывающей `]`, и `[` - буква.

    Как у `fnmatch`: `!` сразу после `[` - отрицание, `]` первым символом - буква.
    """
    k = start + 1
    negated = k < len(segment) and segment[k] == "!"
    if negated:
        k += 1
    body_start = k
    if k < len(segment) and segment[k] == "]":
        k += 1
    while k < len(segment) and segment[k] != "]":
        k += 1
    if k >= len(segment):
        return None
    body = segment[body_start:k]
    ranges: list[tuple[str, str]] = []
    m = 0
    while m < len(body):
        if m + 2 < len(body) and body[m + 1] == "-":
            ranges.append((body[m], body[m + 2]))
            m += 3
        else:
            ranges.append((body[m], body[m]))
            m += 1
    return _CharClass(tuple(ranges), negated), k + 1
