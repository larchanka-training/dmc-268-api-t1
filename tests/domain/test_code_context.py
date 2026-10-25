"""Язык файла и выбор AST-окружения — на литеральных контурах и hunk'ах."""

import pytest

from app.domain.code_context import language_for_path, select_scopes, touched_lines
from app.domain.entities import Definition, FileOutline, Hunk
from app.domain.enums import DefinitionKind, Language

REPO = Definition(DefinitionKind.CLASS, "Repo", "class Repo(Base)", line=10, end_line=40)
SAVE = Definition(
    DefinitionKind.METHOD, "save", "def save(self, item: Item) -> None", 12, 20, parent="Repo"
)
LOAD = Definition(DefinitionKind.METHOD, "load", "def load(self, id: int) -> Item", 22, 40, "Repo")
HELPER = Definition(DefinitionKind.FUNCTION, "helper", "def helper() -> int", 45, 50)
OUTLINE = FileOutline(definitions=(REPO, SAVE, LOAD, HELPER), imports=("import os",))


@pytest.mark.parametrize(
    ("path", "language"),
    [
        ("app/core.py", Language.PYTHON),
        ("stubs/core.pyi", Language.PYTHON),
        ("web/src/App.tsx", Language.TSX),
        ("web/src/api.ts", Language.TYPESCRIPT),
        ("web/src/types.d.ts", Language.TYPESCRIPT),
        ("web/src/legacy.JS", Language.JAVASCRIPT),
        ("web/src/view.jsx", Language.JAVASCRIPT),
        ("cmd/main.go", Language.GO),
        ("README.rst", None),
        ("Makefile", None),
    ],
)
def test_language_for_path(path: str, language: Language | None) -> None:
    assert language_for_path(path) is language


def test_change_inside_method_selects_method_and_its_class() -> None:
    assert select_scopes(OUTLINE, frozenset({15})) == (REPO, SAVE)


def test_definitions_outside_touched_lines_are_dropped() -> None:
    assert select_scopes(OUTLINE, frozenset({47})) == (HELPER,)


def test_module_level_change_selects_nothing() -> None:
    assert select_scopes(OUTLINE, frozenset({1, 2})) == ()


def test_boundary_lines_are_inclusive() -> None:
    assert select_scopes(OUTLINE, frozenset({50})) == (HELPER,)
    assert select_scopes(OUTLINE, frozenset({10})) == (REPO,)


def test_scopes_come_in_file_order_whatever_the_outline_order() -> None:
    shuffled = FileOutline(definitions=(HELPER, LOAD, SAVE, REPO))
    assert select_scopes(shuffled, frozenset({15, 25, 46})) == (REPO, SAVE, LOAD, HELPER)


def test_go_method_pulls_in_its_receiver_type() -> None:
    """Метод Go лежит вне диапазона структуры — тип находится по родителю."""
    repo = Definition(DefinitionKind.CLASS, "Repo", "type Repo struct", 3, 5)
    save = Definition(DefinitionKind.METHOD, "Save", "func (r *Repo) Save()", 7, 10, "Repo")
    other = Definition(DefinitionKind.CLASS, "Other", "type Other struct", 12, 14)
    outline = FileOutline(definitions=(repo, save, other))
    assert select_scopes(outline, frozenset({8})) == (repo, save)


@pytest.mark.parametrize("kind", [DefinitionKind.FUNCTION, DefinitionKind.CLASS, DefinitionKind.INTERFACE])
def test_only_method_pulls_in_type_by_parent_name(kind: DefinitionKind) -> None:
    """У вложенной функции или класса `parent` — охватывающее определение, не тип."""
    money = Definition(DefinitionKind.INTERFACE, "Money", "interface Money", 1, 3)
    outer = Definition(DefinitionKind.FUNCTION, "Money", "function Money(): Money", 5, 9)
    inner = Definition(kind, "round", "round", 6, 7, "Money")
    outline = FileOutline(definitions=(money, outer, inner))
    assert select_scopes(outline, frozenset({6})) == (outer, inner)


def test_added_lines_are_touched() -> None:
    hunk = Hunk("a.py", 1, 3, 1, 4, changed_new_lines=frozenset({2, 3}))
    assert touched_lines([hunk]) == frozenset({2, 3})


def test_deletion_only_hunk_touches_its_new_range() -> None:
    hunk = Hunk("a.py", 10, 4, 10, 3, changed_old_lines=frozenset({12}))
    assert touched_lines([hunk]) == frozenset({10, 11, 12})


def test_deletion_without_context_touches_the_line_before() -> None:
    hunk = Hunk("a.py", 5, 1, 4, 0, changed_old_lines=frozenset({5}))
    assert touched_lines([hunk]) == frozenset({4})


def test_hunks_are_unioned() -> None:
    hunks = [
        Hunk("a.py", 1, 1, 1, 1, changed_new_lines=frozenset({1})),
        Hunk("a.py", 30, 1, 30, 1, changed_new_lines=frozenset({30})),
    ]
    assert touched_lines(hunks) == frozenset({1, 30})


def test_large_file_is_selected_without_quadratic_blowup() -> None:
    """Новый файл: 5000 добавленных строк и 2000 определений — доли секунды."""
    definitions = tuple(
        Definition(DefinitionKind.FUNCTION, f"f{i}", f"def f{i}()", 2 * i + 1, 2 * i + 2)
        for i in range(2000)
    )
    touched = frozenset(range(1, 5001))
    assert len(select_scopes(FileOutline(definitions=definitions), touched)) == 2000
