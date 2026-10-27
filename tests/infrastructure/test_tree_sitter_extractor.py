"""Адаптер tree-sitter: контур файла по фикстуре на каждый язык.

Ожидаемые строки и сигнатуры выписаны по фикстурам вручную, а не
пересчитаны тем же обходом.
"""

import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.code_context import select_scopes
from app.domain.entities import Definition, FileOutline
from app.domain.enums import DefinitionKind, Language
from app.infrastructure.parsing.tree_sitter_extractor import (
    TreeSitterDefinitionExtractor,
)

REPO_ROOT = Path(__file__).parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "outline"
LARGE_FILE_FUNCTIONS = 300

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


# Декораторы TypeScript: у метода они — соседи в теле класса, у
# `@Component(…) export class` — соседи в `export_statement`, а не дети узла.
DECORATED_TS = """\
@Component({
  selector: "app-root",
})
export class AppComponent {
  @Input() @Output()
  title = "app";

  @HostListener("window:resize", [
    "$event",
  ])
  // Пересчёт раскладки.
  @Throttle(100)
  onResize(event: UIEvent): void {
    this.layout();
  }

  @Debounce(300) save = async (): Promise<void> => {};

  layout(): void {}
}
"""


def test_typescript_decorators_are_part_of_definition(
    extractor: TreeSitterDefinitionExtractor,
) -> None:
    outline = extractor.extract(DECORATED_TS, Language.TYPESCRIPT)
    assert outline.definitions == (
        Definition(CLASS, "AppComponent", "class AppComponent", 1, 20),
        # Диапазон — от первого декоратора; сигнатура — без декораторов.
        Definition(
            METHOD, "onResize", "onResize(event: UIEvent): void", 8, 15, "AppComponent"
        ),
        Definition(
            METHOD, "save", "save = async (): Promise<void> =>", 17, 17, "AppComponent"
        ),
        Definition(METHOD, "layout", "layout(): void", 19, 19, "AppComponent"),
    )


GENERIC_GO = """\
package p

func Map[T any, U comparable](xs []T, f func(T) U) []U {
\treturn nil
}

func Keys[
\tK comparable,
\tV interface{ ~int | ~string },
](m map[K]V) []K {
\treturn nil
}

type Set[T interface{ ~int | ~string }] struct {
\titems map[T]struct{}
}

type Ordered[T interface{ ~int }] interface {
\tLess(other T) bool
}

func (s *Set[T]) Add(v T) {
\ts.items[v] = struct{}{}
}
"""


def test_go_generic_signatures_keep_type_parameters(
    extractor: TreeSitterDefinitionExtractor,
) -> None:
    outline = extractor.extract(GENERIC_GO, Language.GO)
    assert outline.definitions == (
        Definition(
            FUNCTION,
            "Map",
            "func Map[T any, U comparable](xs []T, f func(T) U) []U",
            3,
            5,
        ),
        Definition(
            FUNCTION,
            "Keys",
            "func Keys[K comparable, V interface{ ~int | ~string },](m map[K]V) []K",
            7,
            12,
        ),
        # Ограничение с `{` не обрывает параметры типа.
        Definition(
            CLASS, "Set", "type Set[T interface{ ~int | ~string }] struct", 14, 16
        ),
        Definition(
            INTERFACE, "Ordered", "type Ordered[T interface{ ~int }] interface", 18, 20
        ),
        Definition(METHOD, "Add", "func (s *Set[T]) Add(v T)", 22, 24, "Set"),
    )


# Слияние объявлений TS: тип и функция-компаньон с одним именем.
COMPANION_TS = """\
export interface Money {
  amount: number;
}

export function Money(amount: number): Money {
  const round = (x: number): number => Math.round(x);
  return { amount: round(amount) };
}
"""


def test_nested_function_does_not_pull_in_type_by_parent_name(
    extractor: TreeSitterDefinitionExtractor,
) -> None:
    outline = extractor.extract(COMPANION_TS, Language.TYPESCRIPT)
    scopes = select_scopes(outline, frozenset({6}))
    # `round` — функция внутри функции `Money`, а не метод типа `Money`.
    assert [(d.kind, d.name) for d in scopes] == [
        (FUNCTION, "Money"),
        (FUNCTION, "round"),
    ]


# Код дочернего процесса: извлечь контур, дважды отсортировать (как `extract`
# и `select_scopes`) и вывести число определений и все диапазоны строк.
LARGE_FILE_SCRIPT = """\
import sys
from pathlib import Path

from app.domain.code_context import select_scopes
from app.domain.enums import Language
from app.infrastructure.parsing.tree_sitter_extractor import TreeSitterDefinitionExtractor

source = Path(sys.argv[1]).read_text(encoding="utf-8")
outline = TreeSitterDefinitionExtractor().extract(source, Language.PYTHON)
ordered = sorted(outline.definitions, key=lambda d: (d.line, -d.end_line))
select_scopes(outline, frozenset({1}))
select_scopes(outline, frozenset(range(1, source.count("\\n") + 2)))
print(len(ordered))
for d in ordered:
    print(d.line, d.end_line)
"""


def test_large_file_does_not_crash_interpreter(tmp_path: Path) -> None:
    """Контур файла длиннее 256 строк не роняет процесс (#226).

    В tree-sitter 0.26.0 геттеры `Point.row`/`Point.column` отдают
    заимствованную ссылку: для строк больше 256 (их int Python не кэширует)
    число освобождается раньше времени, `Definition.line`/`end_line` указывают
    на освобождённую память, и следующая сортировка падает с segfault
    (SIGSEGV: `returncode -11`, в shell — 139).
    Исправлено в https://github.com/tree-sitter/py-tree-sitter/pull/466.

    Прогон идёт в дочернем процессе: segfault в процессе pytest убил бы весь
    прогон, а не один тест. Проверка диапазонов ловит испорченные значения,
    даже когда раскладка памяти не довела до падения: исходник сгенерирован,
    поэтому строки каждого определения известны точно.
    """
    # Каждая функция — три строки: `def`, `return`, пустая; итого 900 строк.
    source = "".join(
        f"def f{i}():\n    return {i}\n\n" for i in range(LARGE_FILE_FUNCTIONS)
    )
    path = tmp_path / "large.py"
    path.write_text(source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-X", "faulthandler", "-c", LARGE_FILE_SCRIPT, str(path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    count, *ranges = result.stdout.splitlines()
    assert int(count) == LARGE_FILE_FUNCTIONS
    assert ranges == [f"{3 * i + 1} {3 * i + 2}" for i in range(LARGE_FILE_FUNCTIONS)]
