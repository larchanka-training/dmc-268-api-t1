"""Адаптер `DefinitionExtractor` на tree-sitter: Python, TypeScript/TSX,
JavaScript, Go.

Переводит синтаксическое дерево в доменный `FileOutline`: определения
(классы, интерфейсы, функции, методы) с сигнатурой и диапазоном строк и
импорты файла. Что из контура показать модели, решает домен
(`select_scopes`), не адаптер. Порт не импортируется — совпадение
структурное.

tree-sitter терпим к ошибкам: файл с синтаксической ошибкой разбирается
частично, узлы `ERROR` обходятся как обычные — адаптер отдаёт то, что
удалось распознать, и не бросает. Обход итеративный: глубина дерева не
упирается в лимит рекурсии Python.
"""

import re
from dataclasses import dataclass

import tree_sitter
import tree_sitter_go
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript

from app.domain.entities import Definition, FileOutline
from app.domain.enums import DefinitionKind, Language

_GRAMMARS = {
    Language.PYTHON: tree_sitter_python.language,
    Language.TYPESCRIPT: tree_sitter_typescript.language_typescript,
    Language.TSX: tree_sitter_typescript.language_tsx,
    Language.JAVASCRIPT: tree_sitter_javascript.language,
    Language.GO: tree_sitter_go.language,
}

_CLASSES = frozenset({"class_definition", "class_declaration", "abstract_class_declaration"})
_INTERFACES = frozenset({"interface_declaration"})
# Функция становится методом, если её ближайший охватывающий — класс.
_FUNCTIONS = frozenset(
    {"function_definition", "function_declaration", "generator_function_declaration"}
)
_METHODS = frozenset({"method_definition", "abstract_method_signature", "method_signature"})
# Поле класса или переменная, значение которых — функция: `handle = () => {}`.
_FUNCTION_HOLDERS = frozenset({"public_field_definition", "field_definition", "variable_declarator"})
_FUNCTION_VALUES = frozenset({"arrow_function", "function_expression", "function"})
# Узлы, которые могут стоять между декораторами и определением.
_DECORATOR_RUN = frozenset({"decorator", "comment"})
_IMPORTS = frozenset({"import_statement", "import_from_statement", "future_import_statement"})

_MAX_SIGNATURE_CHARS = 300
_MAX_IMPORT_CHARS = 300
_WHITESPACE = re.compile(r"\s+")
# Многострочный список параметров после схлопывания: `( self, x, )` → `(self, x,)`.
_INNER_PAREN_SPACE = re.compile(r"(?<=[(\[])\s+|\s+(?=[)\]])")


@dataclass(frozen=True, slots=True)
class _Scope:
    name: str
    kind: DefinitionKind


class TreeSitterDefinitionExtractor:
    """Контур файла по исходнику; парсер на язык создаётся один раз."""

    def __init__(self) -> None:
        self._parsers = {
            language: tree_sitter.Parser(tree_sitter.Language(grammar()))
            for language, grammar in _GRAMMARS.items()
        }

    def extract(self, source: str, language: Language) -> FileOutline:
        data = source.encode("utf-8")
        root = self._parsers[language].parse(data).root_node
        definitions: list[Definition] = []
        imports: list[str] = []
        stack: list[tuple[tree_sitter.Node, _Scope | None]] = [(root, None)]
        while stack:
            node, enclosing = stack.pop()
            if node.type in _IMPORTS:
                imports.append(_clip(_text(data, node), _MAX_IMPORT_CHARS))
                continue
            if node.type == "import_spec":
                imports.append(_clip("import " + _text(data, node), _MAX_IMPORT_CHARS))
                continue
            definition = _definition(data, node, enclosing)
            if definition is not None:
                definitions.append(definition)
                enclosing = _Scope(definition.name, definition.kind)
            # В обратном порядке: стек отдаёт детей в порядке файла.
            stack.extend((child, enclosing) for child in reversed(node.named_children))
        definitions.sort(key=lambda d: (d.line, -d.end_line))
        return FileOutline(definitions=tuple(definitions), imports=tuple(imports))


def _definition(
    data: bytes, node: tree_sitter.Node, enclosing: _Scope | None
) -> Definition | None:
    """Определение, если узел его объявляет; иначе `None`."""
    parent = enclosing.name if enclosing is not None else None
    in_class = enclosing is not None and enclosing.kind in (
        DefinitionKind.CLASS,
        DefinitionKind.INTERFACE,
    )
    kind: DefinitionKind
    body: tree_sitter.Node | None
    if node.type in _CLASSES:
        kind, body = DefinitionKind.CLASS, node.child_by_field_name("body")
    elif node.type in _INTERFACES:
        kind, body = DefinitionKind.INTERFACE, node.child_by_field_name("body")
    elif node.type in _FUNCTIONS:
        kind = DefinitionKind.METHOD if in_class else DefinitionKind.FUNCTION
        body = node.child_by_field_name("body")
    elif node.type in _METHODS:
        kind, body = DefinitionKind.METHOD, node.child_by_field_name("body")
    elif node.type == "method_declaration":
        # Go: метод объявлен вне типа, родитель — тип ресивера.
        kind, body = DefinitionKind.METHOD, node.child_by_field_name("body")
        parent = _receiver_type(data, node)
    elif node.type == "type_spec":
        return _go_type(data, node)
    elif node.type in _FUNCTION_HOLDERS:
        value = node.child_by_field_name("value")
        if value is None or value.type not in _FUNCTION_VALUES:
            return None
        kind = DefinitionKind.METHOD if in_class else DefinitionKind.FUNCTION
        body = value.child_by_field_name("body")
    else:
        return None

    name_node = node.child_by_field_name("name") or node.child_by_field_name("property")
    if name_node is None or name_node.type.endswith("pattern"):
        # Деструктуризация `const {a, b} = …` — не определение с именем.
        return None
    end = body.start_byte if body is not None else node.end_byte
    signature = _signature(data[_signature_start(node) : end])
    return Definition(
        kind=kind,
        name=_text(data, name_node),
        signature=signature,
        line=_first_line(node),
        end_line=node.end_point.row + 1,
        parent=parent,
    )


def _first_line(node: tree_sitter.Node) -> int:
    """Первая строка определения вместе с декораторами.

    Декораторы — часть определения: их правка затрагивает его. Где они
    лежат, зависит от грамматики: дети узла (JS, поле класса TS), соседи
    перед ним в `decorated_definition` (Python), в теле класса (метод TS) и
    в `export_statement` (`@Component(…) export class`). Соседей собираем
    назад до первого узла, который не декоратор и не комментарий.
    """
    start = node
    sibling = node.prev_named_sibling
    while sibling is not None and sibling.type in _DECORATOR_RUN:
        if sibling.type == "decorator":
            start = sibling
        sibling = sibling.prev_named_sibling
    return start.start_point.row + 1


def _signature_start(node: tree_sitter.Node) -> int:
    """Начало заголовка без декораторов-детей узла (JS, поле класса TS)."""
    for child in node.children:
        if child.type not in _DECORATOR_RUN:
            return child.start_byte
    return node.start_byte


def _go_type(data: bytes, node: tree_sitter.Node) -> Definition | None:
    """`type Repo struct {…}` / `type I interface {…}`; прочие типы — не классы."""
    type_node = node.child_by_field_name("type")
    name_node = node.child_by_field_name("name")
    if type_node is None or name_node is None:
        return None
    if type_node.type == "struct_type":
        kind, keyword = DefinitionKind.CLASS, b"struct"
    elif type_node.type == "interface_type":
        kind, keyword = DefinitionKind.INTERFACE, b"interface"
    else:
        return None
    # Заголовок — до узла типа, а не до первой `{`: в параметрах типа
    # бывает ограничение `[T interface{ ~int }]`.
    head = data[node.start_byte : type_node.start_byte]
    return Definition(
        kind=kind,
        name=_text(data, name_node),
        signature=_signature(b"type " + head + keyword),
        line=node.start_point.row + 1,
        end_line=node.end_point.row + 1,
    )


def _receiver_type(data: bytes, node: tree_sitter.Node) -> str | None:
    """Имя типа ресивера Go: `(r *Repo[T])` → `Repo`."""
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None
    stack = [receiver]
    while stack:
        current = stack.pop()
        if current.type == "type_identifier":
            return _text(data, current)
        stack.extend(reversed(current.named_children))
    return None


def _signature(raw: bytes) -> str:
    """Заголовок до тела в одну строку: без хвостового `:`/`{` и лишних пробелов."""
    text = _WHITESPACE.sub(" ", raw.decode("utf-8", errors="replace")).strip()
    text = _INNER_PAREN_SPACE.sub("", text)
    return _clip(text.rstrip(":{").rstrip(), _MAX_SIGNATURE_CHARS)


def _text(data: bytes, node: tree_sitter.Node) -> str:
    raw = data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    return _WHITESPACE.sub(" ", raw).strip()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
