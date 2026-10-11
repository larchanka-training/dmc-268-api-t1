"""Адаптер tree-sitter: контур файла по фикстуре на каждый язык.

Ожидаемые строки и сигнатуры выписаны по фикстурам вручную, а не
пересчитаны тем же обходом.
"""

from pathlib import Path

import pytest

from app.domain.entities import Definition, FileOutline
from app.domain.enums import DefinitionKind, Language
from app.infrastructure.parsing.tree_sitter_extractor import (
    TreeSitterDefinitionExtractor,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "outline"

CLASS = DefinitionKind.CLASS
INTERFACE = DefinitionKind.INTERFACE
FUNCTION = DefinitionKind.FUNCTION
METHOD = DefinitionKind.METHOD


@pytest.fixture(scope="module")
def extractor() -> TreeSitterDefinitionExtractor:
    return TreeSitterDefinitionExtractor()


def _outline(extractor: TreeSitterDefinitionExtractor, name: str, language: Language) -> FileOutline:
    return extractor.extract((FIXTURES / name).read_text(encoding="utf-8"), language)


def test_python_outline(extractor: TreeSitterDefinitionExtractor) -> None:
    outline = _outline(extractor, "sample.py", Language.PYTHON)
    assert outline.definitions == (
        # Диапазон класса начинается с декоратора.
        Definition(CLASS, "Repo", "class Repo(Base, Mixin)", 5, 19),
        Definition(METHOD, "save", "def save(self, item: Item) -> None", 9, 10, "Repo"),
        Definition(METHOD, "load", "async def load(self, item_id: int,) -> Item", 12, 19, "Repo"),
        Definition(FUNCTION, "inner", "def inner() -> int", 16, 17, "load"),
        Definition(FUNCTION, "helper", "def helper(values: Iterable[int]) -> int", 22, 25),
    )
    assert outline.imports == ("import os", "from collections.abc import Iterable", "import json")


def test_typescript_outline(extractor: TreeSitterDefinitionExtractor) -> None:
    outline = _outline(extractor, "sample.ts", Language.TYPESCRIPT)
    assert outline.definitions == (
        Definition(CLASS, "Foo", "abstract class Foo extends Bar implements Baz", 4, 14),
        Definition(METHOD, "handle", "handle = async (x: number): Promise<void> =>", 5, 7, "Foo"),
        Definition(METHOD, "run", "run(a: string): void", 9, 11, "Foo"),
        Definition(METHOD, "go", "abstract go(): void", 13, 13, "Foo"),
        Definition(INTERFACE, "Store", "interface Store", 16, 18),
        Definition(METHOD, "get", "get(id: string): string", 17, 17, "Store"),
        Definition(FUNCTION, "double", "double = (a: number): number =>", 20, 20),
        Definition(FUNCTION, "identity", "function identity<T>(x: T): T", 22, 24),
    )
    assert outline.imports == ('import { Bar } from "./bar";', 'import type { Baz } from "./baz";')


def test_tsx_grammar_parses_typescript(extractor: TreeSitterDefinitionExtractor) -> None:
    tsx = _outline(extractor, "sample.ts", Language.TSX)
    assert [d.name for d in tsx.definitions][:3] == ["Foo", "handle", "run"]


def test_javascript_outline(extractor: TreeSitterDefinitionExtractor) -> None:
    outline = _outline(extractor, "sample.js", Language.JAVASCRIPT)
    assert outline.definitions == (
        Definition(CLASS, "Handler", "class Handler extends Base", 4, 10),
        Definition(METHOD, "constructor", "constructor(app)", 5, 7, "Handler"),
        Definition(METHOD, "onRequest", "onRequest = (req, res) =>", 9, 9, "Handler"),
        Definition(FUNCTION, "legacy", "legacy = function (a, b)", 12, 14),
        Definition(FUNCTION, "ids", "function* ids()", 16, 18),
    )
    # `require` — вызов функции, а не объявление импорта: не попадает.
    assert outline.imports == ('import express from "express";',)


def test_go_outline(extractor: TreeSitterDefinitionExtractor) -> None:
    outline = _outline(extractor, "sample.go", Language.GO)
    assert outline.definitions == (
        Definition(CLASS, "Repo", "type Repo struct", 8, 10),
        Definition(INTERFACE, "Saver", "type Saver interface", 12, 14),
        # Родитель метода Go — тип ресивера, хотя метод объявлен вне типа.
        Definition(METHOD, "Save", "func (r *Repo) Save(x int) error", 16, 19, "Repo"),
        Definition(FUNCTION, "New", "func New() *Repo", 21, 23),
    )
    assert outline.imports == ('import "fmt"', 'import alias "example.com/x/y"')


def test_syntax_error_does_not_break_extraction(extractor: TreeSitterDefinitionExtractor) -> None:
    source = "def broken(:\n    pass\n\nclass Ok:\n    def m(self):\n        pass\n"
    outline = extractor.extract(source, Language.PYTHON)
    assert ("Ok", CLASS) in [(d.name, d.kind) for d in outline.definitions]
    assert ("m", METHOD) in [(d.name, d.kind) for d in outline.definitions]


def test_destructuring_is_not_a_definition(extractor: TreeSitterDefinitionExtractor) -> None:
    outline = extractor.extract("const { a, b } = () => 1;\n", Language.JAVASCRIPT)
    assert outline.definitions == ()


def test_empty_source_has_empty_outline(extractor: TreeSitterDefinitionExtractor) -> None:
    assert extractor.extract("", Language.GO) == FileOutline()


def test_long_signature_is_clipped(extractor: TreeSitterDefinitionExtractor) -> None:
    params = ", ".join(f"a{i}: int" for i in range(100))
    (definition,) = extractor.extract(f"def f({params}) -> None:\n    pass\n", Language.PYTHON).definitions
    assert len(definition.signature) == 300
    assert definition.signature.endswith("…")
