"""AST-окружение изменённых строк: какой язык у файла и какие определения
охватывают изменение.

Парсер (порт `DefinitionExtractor`) отдаёт контур всего файла; решение, что
из контура показать модели, принимается здесь — чистыми функциями над
hunk'ами и контуром, поэтому тестируется на литералах без tree-sitter.
"""

from bisect import bisect_left
from collections.abc import Iterable
from pathlib import PurePosixPath

from app.domain.entities import Definition, FileOutline, Hunk
from app.domain.enums import DefinitionKind, Language

# Реестр «расширение → грамматика»: добавить язык — запись здесь и грамматика
# в адаптере. Общий с индексом определений (change add-tree-sitter-index).
LANGUAGE_BY_SUFFIX: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".ts": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".cts": Language.TYPESCRIPT,
    ".tsx": Language.TSX,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".go": Language.GO,
}


_TYPE_KINDS = frozenset({DefinitionKind.CLASS, DefinitionKind.INTERFACE})


def language_for_path(path: str) -> Language | None:
    """Язык по расширению; `None` — грамматики нет, файл идёт без AST."""
    return LANGUAGE_BY_SUFFIX.get(PurePosixPath(path).suffix.lower())


def touched_lines(hunks: Iterable[Hunk]) -> frozenset[int]:
    """Строки новой версии файла, которые затронул дифф.

    Добавленные строки — как есть. У hunk'а только с удалениями изменённых
    строк новой стороны нет, и место удаления обозначает его диапазон новой
    стороны (контекст вокруг удалённого), а если и он пуст — строка, после
    которой удалено.
    """
    lines: set[int] = set()
    for hunk in hunks:
        if hunk.changed_new_lines:
            lines |= hunk.changed_new_lines
        elif hunk.new_count > 0:
            lines.update(range(hunk.new_start, hunk.new_start + hunk.new_count))
        elif hunk.new_start > 0:
            lines.add(hunk.new_start)
    return frozenset(lines)


def select_scopes(outline: FileOutline, touched: frozenset[int]) -> tuple[Definition, ...]:
    """Определения, охватывающие затронутые строки, и их родительские типы.

    Класс охватывает свои методы по диапазону строк, поэтому изменение в
    методе Python/TS даёт и метод, и его класс. Метод Go объявлен вне
    структуры — её тип добавляется по имени родителя (ресивера); только для
    метода, не для вложенных функций и классов. Результат — в порядке файла.
    """
    lines = sorted(touched)

    def covers(d: Definition) -> bool:
        # Первая затронутая строка не раньше начала определения — внутри ли она.
        at = bisect_left(lines, d.line)
        return at < len(lines) and lines[at] <= d.end_line

    ordered = sorted(outline.definitions, key=lambda d: (d.line, -d.end_line))
    selected = {d for d in ordered if covers(d)}
    # По имени — только тип метода: у вложенной функции или класса `parent` —
    # охватывающее определение, и тип с тем же именем к изменению не относится.
    parents = {d.parent for d in selected if d.kind is DefinitionKind.METHOD and d.parent}
    return tuple(
        d
        for d in ordered
        if d in selected
        or (d.kind in _TYPE_KINDS and d.parent is None and d.name in parents)
    )
