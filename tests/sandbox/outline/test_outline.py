"""Песочница tree-sitter: входы (`--file`/`--lines`, ключ `lines:` кейса)."""

import dataclasses
import json
import os
import random
import shutil
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest
import yaml

from app.config import Settings
from app.domain.code_context import touched_lines
from app.domain.diff_parser import parse_diff
from app.domain.entities import FileOutline
from app.domain.enums import Language

from ...fakes.github_api import file_diff
from ..inputs import InputError, PrInput, load_case_file
from ..worker import SandboxConfig
from .__main__ import _print_pr, _problems, compress_lines, main
from .grammars import main as grammars_main
from .runner import _RecordingExtractor, run_outline
from .sources import (
    OutlinePr,
    from_file,
    load_outline_case,
    parse_lines,
    touched_diff,
    with_touched,
)


def _touched(pr: PrInput, path: str) -> frozenset[int]:
    """Затронутые строки файла в диффе, который увидит этап сборки контекста."""
    diffs = pr.diffs if isinstance(pr, OutlinePr) else {}
    diff = diffs.get(path) or file_diff(path, pr.base_files.get(path), pr.head_files[path])
    [parsed] = parse_diff(diff)
    return touched_lines(parsed.hunks)


@pytest.mark.parametrize(
    ("spec", "expected"),
    [("3", {3}), ("2-4", {2, 3, 4}), ("1, 5-6", {1, 5, 6}), ("10-10", {10})],
)
def test_parse_lines_accepts_numbers_ranges_and_lists(
    spec: str, expected: set[int]
) -> None:
    assert parse_lines(spec, 10) == frozenset(expected)


@pytest.mark.parametrize("spec", ["0", "5-3", "x", "11", "9-11", "", "1,,2", "-3"])
def test_parse_lines_rejects_bad_spec(spec: str) -> None:
    with pytest.raises(InputError):
        parse_lines(spec, 10)


def test_from_file_without_lines_is_a_new_file(tmp_path: Path, monkeypatch) -> None:
    """Без `--lines` файл новый: база пустая, путь - относительно текущего каталога."""
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "m.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    pr = from_file(tmp_path / "pkg" / "m.py", None)

    assert pr.head_files == {"pkg/m.py": "a = 1\nb = 2\n"}
    assert pr.base_files == {}
    assert pr.title == "file pkg/m.py"
    assert _touched(pr, "pkg/m.py") == {1, 2}


def test_from_file_with_lines_touches_exactly_the_range(
    tmp_path: Path, monkeypatch
) -> None:
    source = "".join(f"x{n} = {n}\n" for n in range(1, 8))
    (tmp_path / "m.py").write_text(source, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    pr = from_file(tmp_path / "m.py", "3-4")

    assert pr.head_files == {"m.py": source}
    assert _touched(pr, "m.py") == {3, 4}


def test_from_file_lines_on_big_file_with_blank_borders(
    tmp_path: Path, monkeypatch
) -> None:
    """Пустые строки на границах диапазона не сдвигают дифф: затронут ровно диапазон."""
    blocks = [f"def f{n}():\n    return {n}\n\n\n" for n in range(100)]
    source = "".join(blocks)
    assert len(source.splitlines()) >= 300
    (tmp_path / "big.py").write_text(source, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # 123-124 и 131-132 - одинаковые пары пустых строк: без заглушек difflib сдвинул бы
    # вставку на 125-132.
    pr = from_file(tmp_path / "big.py", "123-130")

    assert _touched(pr, "big.py") == set(range(123, 131))


def test_parse_lines_names_reversed_range() -> None:
    with pytest.raises(InputError, match="перевёрнут"):
        parse_lines("10-5", 20)


# Хвост или промежуток, совпадающий с концом диапазона: difflib по тексту
# сопоставил бы его с такой же строкой внутри диапазона (PR #236).
_SHIFTING = [
    # Неизменённая `...` сразу после диапазона, последняя строка диапазона - тоже `...`.
    ("def a():\n    ...\n    ...\n", "1-2"),
    # То же для одиночной строки.
    ("x = 1\n    ...\n    ...\n", "2"),
    # Пустая строка в промежутке двух диапазонов.
    ("a = 1\n\n\n\nb = 2\n", "1-2, 4-5"),
]


@pytest.mark.parametrize(("source", "spec"), _SHIFTING)
def test_from_file_lines_do_not_shift_onto_equal_lines(
    tmp_path: Path, monkeypatch, source: str, spec: str
) -> None:
    (tmp_path / "m.py").write_text(source, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    pr = from_file(tmp_path / "m.py", spec)

    assert _touched(pr, "m.py") == parse_lines(spec, len(source.splitlines()))
    # Фейковый GitHub по тексту ошибся бы - иначе тест ничего не ловит.
    [old] = parse_diff(file_diff("m.py", pr.base_files["m.py"], source))
    assert touched_lines(old.hunks) != _touched(pr, "m.py")


def test_touched_diff_is_exact_on_random_ranges_of_repo_files() -> None:
    """Случайные диапазоны, одиночные и парами, по файлам репозитория."""
    rnd = random.Random(236)
    files = sorted(p for p in (REPO_ROOT / "app").rglob("*.py") if p.stat().st_size)
    for _ in range(300):
        path = rnd.choice(files)
        text = path.read_text(encoding="utf-8")
        total = len(text.splitlines())
        start = rnd.randint(1, total)
        lines = set(range(start, rnd.randint(start, min(total, start + 20)) + 1))
        if rnd.random() < 0.5:
            other = rnd.randint(1, total)
            lines |= set(range(other, min(total, other + 5) + 1))
        diff = touched_diff("m.py", text, frozenset(lines))
        [parsed] = parse_diff(diff)
        assert touched_lines(parsed.hunks) == lines, (path, sorted(lines))


def test_from_file_outside_cwd_keeps_absolute_path(tmp_path: Path, monkeypatch) -> None:
    """`../dir/m.py` - без `..` в пути PR: фейковый GitHub его находит."""
    (tmp_path / "dir").mkdir()
    (tmp_path / "dir" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "cwd").mkdir()
    monkeypatch.chdir(tmp_path / "cwd")

    pr = from_file(Path("../dir/m.py"), None)

    rel = (tmp_path / "dir" / "m.py").resolve().as_posix()
    assert list(pr.head_files) == [rel]
    [file] = run_outline([pr], SandboxConfig()).prs[0].files
    assert (file.status, file.definitions) == ("in_context", 1)


def test_from_file_rejects_missing_and_binary(tmp_path: Path) -> None:
    with pytest.raises(InputError, match="нет"):
        from_file(tmp_path / "absent.py", None)
    (tmp_path / "bin.py").write_bytes(b"\xff\xfe\x00")
    with pytest.raises(InputError, match="UTF-8"):
        from_file(tmp_path / "bin.py", None)


def _deny_read(monkeypatch, name: str) -> None:
    """`read_text` файла `name` падает как без прав: от root и `chmod` не зависит."""
    real = Path.read_text

    def read_text(self: Path, *args, **kwargs) -> str:
        if self.name == name:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)


def test_main_unreadable_file_is_input_error(tmp_path: Path, monkeypatch, capsys) -> None:
    """Нечитаемый `--file` - код 2 и сообщение, а не traceback и код 1."""
    (tmp_path / "m.py").write_text("a = 1\n", encoding="utf-8")
    _deny_read(monkeypatch, "m.py")

    code = main(["--file", str(tmp_path / "m.py")])

    err = capsys.readouterr().err
    assert code == 2
    assert "m.py не прочитать: Permission denied" in err
    assert "Traceback" not in err


def test_load_outline_case_unreadable_is_input_error(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "case.yaml").write_text("files: []\n", encoding="utf-8")
    _deny_read(monkeypatch, "case.yaml")
    with pytest.raises(InputError, match="case.yaml не прочитать: Permission denied"):
        load_outline_case(tmp_path / "case.yaml")


def test_from_file_lines_rejects_other_line_separators(tmp_path: Path) -> None:
    """`\\x0c` - разделитель для `str.splitlines()`: номера строк разошлись бы с редактором."""
    (tmp_path / "m.py").write_text("a = 1\n\x0c\nb = 2\nc = 3\n", encoding="utf-8")
    with pytest.raises(InputError, match="разделители строк"):
        from_file(tmp_path / "m.py", "2")


def test_from_file_rejects_lines_past_the_end(tmp_path: Path) -> None:
    (tmp_path / "m.py").write_text("a = 1\n", encoding="utf-8")
    with pytest.raises(InputError, match=r"файл .*m\.py: строки «2» вне файла"):
        from_file(tmp_path / "m.py", "2")


CASE = """\
title: Кейс контура
pull_request: {title: t, description: d}
files:
  a.py: |
    x = 1
    y = 2
    z = 3
  b.md: |
    текст
"""


def test_load_outline_case_lines_key_builds_base(tmp_path: Path) -> None:
    path = tmp_path / "case.yaml"
    path.write_text(CASE + "lines:\n  a.py: 2\n", encoding="utf-8")

    pr = load_outline_case(path)

    assert set(pr.base_files) == {"a.py"}
    assert _touched(pr, "a.py") == {2}
    assert _touched(pr, "b.md") == {1}


def test_load_outline_case_without_lines_is_all_new(tmp_path: Path) -> None:
    path = tmp_path / "case.yaml"
    path.write_text(CASE, encoding="utf-8")

    assert load_outline_case(path).base_files == {}


@pytest.mark.parametrize(
    ("lines", "match"),
    [
        ("lines:\n  c.py: '1'\n", "c.py"),
        ("lines:\n  a.py: '4'\n", "a.py"),
        ("lines: [a.py]\n", "словарём"),
        ("lines: []\n", "словарём"),
        ("lines: ''\n", "словарём"),
        ("lines: 0\n", "словарём"),
        ("lines:\n  a.py: [1]\n", "a.py"),
    ],
    ids=[
        "path-not-in-files",
        "past-the-end",
        "not-a-mapping",
        "empty-list",
        "empty-string",
        "zero",
        "not-a-spec",
    ],
)
def test_load_outline_case_rejects_bad_lines(
    tmp_path: Path, lines: str, match: str
) -> None:
    path = tmp_path / "case.yaml"
    path.write_text(CASE + lines, encoding="utf-8")

    with pytest.raises(InputError, match=match):
        load_outline_case(path)


@pytest.mark.parametrize("text", ["- a\n- b\n", "просто строка\n", ""], ids=["list", "scalar", "empty"])
def test_load_outline_case_not_a_mapping_is_input_error(tmp_path: Path, text: str) -> None:
    """YAML не словарём - `InputError` (код 2), а не `AttributeError` на `raw.get`."""
    path = tmp_path / "case.yaml"
    path.write_text(text, encoding="utf-8")

    with pytest.raises(InputError, match="не разобрать"):
        load_outline_case(path)


def test_load_outline_case_lines_rejects_other_line_separators(tmp_path: Path) -> None:
    """Имя файла в сообщении - один раз: «кейс …: a.py: разделители …»."""
    path = tmp_path / "case.yaml"
    path.write_text(
        'title: t\npull_request: {title: t, description: d}\nfiles:\n  a.py: "x\\fy\\nz\\n"\n'
        "lines:\n  a.py: '2'\n",
        encoding="utf-8",
    )

    with pytest.raises(InputError, match=r"case\.yaml: a\.py: разделители строк"):
        load_outline_case(path)


SERVICE = """\
from decimal import Decimal


def helper() -> int:
    return 1


class Discount:
    rate = 10

    @staticmethod
    def apply(price: Decimal, d: int) -> Decimal:
        total = price - price * d / 100
        return total
"""


def test_run_outline_reports_what_the_model_sees() -> None:
    """Правка внутри метода: в контекст - метод и его класс, импорты; `.md` и `vendor/` - без AST."""
    pr = PrInput(
        id="one",
        title="Один Python-файл",
        pr_title="t",
        pr_description="d",
        base_files={"app/service.py": with_touched(SERVICE, frozenset({14}))},
        head_files={
            "app/service.py": SERVICE,
            "docs/notes.md": "# Заметки\n",
            "vendor/x.py": "def f():\n    return 1\n",
        },
        expected=(),
    )

    [outline] = run_outline([pr], SandboxConfig()).prs

    assert (outline.id, outline.title, outline.number, outline.error) == (
        "one",
        "Один Python-файл",
        1,
        None,
    )
    files = {f.path: f for f in outline.files}
    service = files["app/service.py"]
    assert service.status == "in_context"
    assert service.language == "python"
    assert service.touched == (14,)
    # Метод - с декоратором (строка 11), сигнатуры - без двоеточия.
    assert (
        service.scopes
        == "8-14 class Discount\n  11-14 def apply(price: Decimal, d: int) -> Decimal"
    )
    assert service.import_text == "from decimal import Decimal"
    # Текст пакета - подмножество контура: смена формата пакета роняет тест, а не дрейфует.
    assert service.scopes is not None
    assert set(service.scopes.split("\n")) <= set(service.outline)
    assert (service.definitions, service.imports) == (3, 1)
    assert service.outline == (
        "4-5 def helper() -> int",
        "8-14 class Discount",
        "  11-14 def apply(price: Decimal, d: int) -> Decimal",
    )
    assert (service.dropped, service.empty_outline, service.reason) == ((), False, None)
    assert files["docs/notes.md"].status == "no_grammar"
    assert files["docs/notes.md"].language is None
    assert files["vendor/x.py"].status == "filtered"
    assert [item["path"] for item in outline.ast] == [
        "app/service.py",
        "app/service.py",
    ]
    assert outline.omitted_count >= len(outline.omitted)
    assert 0 < outline.ast_tokens < outline.token_count <= outline.max_tokens


def _pr(pr_id: str, base: dict[str, str], head: dict[str, str | None]) -> PrInput:
    return PrInput(
        id=pr_id,
        title=pr_id,
        pr_title="t",
        pr_description="d",
        base_files=base,
        head_files=head,
        expected=(),
    )


def _deep_source(depth: int = 12) -> tuple[str, int]:
    """Вложенные функции с длинными сигнатурами; второе - строка тела самой глубокой."""
    # При пакете 1024 и BYTES_PER_TOKEN = 4 граница - между 4 и 5 аргументами (при 4
    # сигнатуры ещё помещаются); семь аргументов дают запас около 40%.
    arguments = ", ".join(f"argument_number_{k}: int" for k in range(7))
    lines = [
        "    " * level
        + f"def level_{level}_function_with_a_long_name({arguments}) -> None:"
        for level in range(depth)
    ]
    lines.append("    " * depth + "value = 1")
    return "\n".join(lines) + "\n", len(lines)


def test_run_outline_budget_survives_trimmed_omitted() -> None:
    """Перечень `omitted` урезан бюджетом, а `dropped` всё равно видит отброшенный AST."""
    deep, body_line = _deep_source()
    filler = {
        f"notes/f{n:02}.txt": "".join(f"строка {n}-{k}\n" for k in range(20))
        for n in range(80)
    }
    pr = _pr(
        "budget",
        {"deep.py": with_touched(deep, frozenset({body_line}))},
        {"deep.py": deep, **filler},
    )

    [outline] = run_outline([pr], SandboxConfig(context_max_tokens=1024)).prs

    deep_report = next(f for f in outline.files if f.path == "deep.py")
    assert outline.omitted_count > len(outline.omitted)
    assert (deep_report.status, deep_report.dropped) == ("budget", ("scopes",))
    assert deep_report.touched == (body_line,)


def test_run_outline_does_not_reuse_outlines_between_prs() -> None:
    """Тот же файл во втором PR не разобран (сверх `ast_max_files`) - контура у него нет."""
    shared = "def shared() -> int:\n    return 1\n"
    others: dict[str, str | None] = {f"b{n:02}.py": f"x{n} = {n}\n" for n in range(20)}
    first = _pr("first", {}, {"z.py": shared})
    second = _pr("second", {}, {**others, "z.py": shared})

    _, outline = run_outline([first, second], SandboxConfig()).prs

    z = next(f for f in outline.files if f.path == "z.py")
    assert (z.status, z.reason, z.definitions, z.outline) == (
        "skipped",
        "file_limit",
        None,
        (),
    )


def test_run_outline_empty_new_file_has_no_hunks() -> None:
    [outline] = run_outline([_pr("empty", {}, {"empty.py": ""})], SandboxConfig()).prs

    [empty] = outline.files
    assert (empty.status, empty.touched, empty.definitions) == ("no_hunks", (), None)


def test_run_outline_drops_deep_scopes_at_minimal_budget() -> None:
    """Сценарий плана: один файл, 12 вложенных `def`, правка в самой глубокой, пакет 1024."""
    deep, body_line = _deep_source()
    pr = _pr(
        "deep",
        {"deep.py": with_touched(deep, frozenset({body_line}))},
        {"deep.py": deep},
    )

    [outline] = run_outline([pr], SandboxConfig(context_max_tokens=1024)).prs

    [report] = outline.files
    assert (report.status, report.dropped, report.scopes) == (
        "budget",
        ("scopes",),
        None,
    )
    assert {"path": "deep.py", "part": "scopes", "reason": "budget"} in outline.omitted
    assert outline.ast == ()
    assert outline.omitted_count >= len(outline.omitted)


def test_run_outline_twins_beyond_file_limit_are_skipped(monkeypatch) -> None:
    """Одинаковый файл сверх `ast_max_files` не получает контур близнеца."""
    monkeypatch.setenv("AST_MAX_FILES", "1")
    util = "def helper() -> int:\n    return 1\n"
    pr = _pr("twins", {}, {"pkg_a/util.py": util, "pkg_b/util.py": util})

    [outline] = run_outline([pr], SandboxConfig()).prs

    files = {f.path: f for f in outline.files}
    assert files["pkg_a/util.py"].definitions == 1
    twin = files["pkg_b/util.py"]
    assert (twin.status, twin.reason, twin.definitions) == ("skipped", "file_limit", None)


def test_run_outline_empty_ast_costs_zero_tokens() -> None:
    [outline] = run_outline([_pr("md", {}, {"a.md": "x\n"})], SandboxConfig()).prs

    assert (outline.ast, outline.ast_tokens) == ((), 0)


@pytest.mark.parametrize("reason", ["missing", "unavailable", "parse_error"])
def test_problems_flag_files_the_fake_github_should_have_served(reason: str) -> None:
    [outline] = run_outline(
        [_pr("p", {}, {"a.py": "def f():\n    return 1\n"})], SandboxConfig()
    ).prs
    [file] = outline.files
    broken = dataclasses.replace(file, status="skipped", reason=reason)

    assert _problems(dataclasses.replace(outline, files=(broken,))) == [
        f"a.py: {reason}"
    ]


def test_run_outline_flags_empty_outline_and_too_large() -> None:
    """Код из одних выражений - пустой контур; файл больше `ast_max_file_bytes` - без AST."""
    huge = "".join(f"def f{n}() -> int:\n    return {n}\n\n\n" for n in range(9_000))
    assert len(huge.encode()) > Settings.model_fields["ast_max_file_bytes"].default
    pr = _pr("problems", {}, {"script.py": "print(1)\nx = 2 + 3\n", "huge.py": huge})

    [outline] = run_outline([pr], SandboxConfig()).prs

    files = {f.path: f for f in outline.files}
    script = files["script.py"]
    assert (script.status, script.empty_outline, script.definitions) == (
        "no_scopes",
        True,
        0,
    )
    big = files["huge.py"]
    assert (big.status, big.reason, big.definitions, big.empty_outline) == (
        "skipped",
        "too_large",
        None,
        False,
    )
    assert outline.omitted_count >= len(outline.omitted)


EXAMPLE = Path(__file__).parent / "examples" / "outline-case.yaml"


def test_example_case_loads_in_worker_sandbox_too() -> None:
    """Ключ `lines:` не мешает песочнице воркера: `load_case_file` его игнорирует."""
    pr = load_case_file(EXAMPLE)

    assert pr.base_files == {}
    assert set(pr.head_files) == {
        "app/billing/discount.py",
        "app/billing/broken.py",
        "web/api/handler.ts",
        "web/ui/Badge.tsx",
        "web/legacy/format.js",
        "services/orders/repo.go",
        "docs/orders.md",
    }


def test_example_case_selects_definitions_per_language() -> None:
    """Мок-PR демо: по языку - что выбрано вокруг правок (литералы сверены запуском)."""
    [outline] = run_outline([load_outline_case(EXAMPLE)], SandboxConfig()).prs

    files = {f.path: f for f in outline.files}
    assert {path: f.status for path, f in files.items()} == {
        "app/billing/discount.py": "in_context",
        "app/billing/broken.py": "in_context",
        "web/api/handler.ts": "in_context",
        "web/ui/Badge.tsx": "in_context",
        "web/legacy/format.js": "in_context",
        "services/orders/repo.go": "in_context",
        "docs/orders.md": "no_grammar",
    }
    # Python: правка в методе под декоратором - метод (с декоратора) и его класс, без __init__.
    assert files["app/billing/discount.py"].scopes == (
        "5-13 class Discount\n  11-13 def apply(price: Decimal, percent: int) -> Decimal"
    )
    # TS: правка в интерфейсе и в поле-стрелке класса.
    assert files["web/api/handler.ts"].scopes == (
        "3-5 interface Handler\n"
        "  4-4 handle(request: Request): string\n"
        "7-13 class EchoHandler implements Handler\n"
        "  10-12 handle = (request: Request): string =>"
    )
    assert (
        files["web/ui/Badge.tsx"].scopes == "5-7 function Badge({ label }: BadgeProps)"
    )
    # JS: стрелка в const - с именем переменной, без `export const`.
    assert files["web/legacy/format.js"].scopes == (
        "3-5 function formatPrice(cents)\n7-9 formatPercent = (value) =>"
    )
    # Go: метод тянет тип ресивера по имени; generic-функция - с параметрами типа.
    assert files["services/orders/repo.go"].scopes == (
        "5-7 type Repo struct\n"
        "  9-11 func (r *Repo) Key(id int) string\n"
        "13-19 func First[T any](items []T) (T, bool)"
    )
    assert files["services/orders/repo.go"].import_text == 'import "fmt"'
    # Синтаксическая ошибка: контур частичный - тело total с ошибкой ушло в сигнатуру,
    # но average разобран отдельно и выбран; parse_error нет.
    broken = files["app/billing/broken.py"]
    assert broken.outline == (
        "1-2 def total(items: list[int]) -> int: return sum(items) +",
        "5-6 def average(items: list[int]) -> float",
    )
    assert broken.scopes == "5-6 def average(items: list[int]) -> float"
    assert (broken.reason, broken.empty_outline) == (None, False)
    assert not any(f.empty_outline for f in outline.files)


# --- CLI ------------------------------------------------------------------

REPO_ROOT = Path(__file__).parents[3]


def test_compress_lines() -> None:
    assert compress_lines((3, 4, 5, 6, 7, 12)) == "3-7, 12"
    assert compress_lines((1,)) == "1"
    assert compress_lines(()) == "-"


def test_main_default_prints_example_report(capsys) -> None:
    assert main([]) == 0

    out = capsys.readouterr().out
    assert out.startswith("песочница tree-sitter: PR 1, правила: rules, лимит пакета: ")
    assert "=== [1] outline-case: Мультиязычный мок-PR" in out
    assert "app/billing/discount.py  python  изменены строки 13" in out
    assert "  в контекст (как видит модель):" in out
    assert "    5-13 class Discount\n      11-13 def apply(" in out
    assert "  импорты:\n    from decimal import Decimal" in out
    assert "docs/orders.md  без грамматики" in out
    assert "пакет: файлов с AST: 6, ~" in out
    assert "проблемы: -" in out
    assert "итого: PR 1, файлов с AST 6, проблем 0" in out


def test_main_full_outline_shows_unselected_definitions(capsys) -> None:
    assert main(["--full-outline"]) == 0

    out = capsys.readouterr().out
    assert "  весь контур:\n    5-13 class Discount\n      8-9 def __init__(" in out


def _module_file(tmp_path: Path) -> Path:
    path = tmp_path / "m.py"
    path.write_text(
        "import os\n\n\ndef f() -> str:\n    return os.sep\n", encoding="utf-8"
    )
    return path


def test_main_file_lines_json_has_fixed_keys(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out.json"

    code = main(
        ["--file", str(_module_file(tmp_path)), "--lines", "5", "--json", str(out)]
    )

    assert code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert set(data) == {"prs"}
    [pr] = data["prs"]
    assert set(pr) == {
        "id", "title", "number", "files", "token_count", "max_tokens",
        "ast_tokens", "omitted", "omitted_count", "ast", "error",
    }  # fmt: skip
    [file] = pr["files"]
    assert set(file) == {
        "path", "language", "status", "reason", "touched", "definitions", "imports",
        "outline", "scopes", "import_text", "dropped", "empty_outline",
    }  # fmt: skip
    assert (file["status"], file["touched"]) == ("in_context", [5])
    assert f"результат: {out}" in capsys.readouterr().out


def test_main_payload_prints_raw_ast_and_omitted(tmp_path: Path, capsys) -> None:
    assert (
        main(["--file", str(_module_file(tmp_path)), "--lines", "5", "--payload"]) == 0
    )

    [pr] = json.loads(capsys.readouterr().out)
    assert set(pr) == {"id", "ast", "omitted", "omitted_count"}
    assert [set(item) for item in pr["ast"]] == [
        {"path", "language", "scopes"},
        {"path", "language", "imports"},
    ]
    assert pr["ast"][0]["scopes"].startswith("<<<untrusted kind=scopes id=")


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--file", "{tmp}/absent.py"], "нет"),
        (["--file", "{file}", "--lines", "9-1"], "строки"),
        (["--lines", "1"], "--lines"),
        (["--file", "{file}", "--file", "{file}", "--lines", "1"], "--lines"),
        (["--context-max-tokens", "100"], "context_max_tokens"),
        (["--window", "1000"], "окн"),
        (["--from-git", "bad"], "BASE..HEAD"),
    ],
    ids=[
        "missing-file",
        "reversed-lines",
        "lines-without-file",
        "lines-with-two-files",
        "tiny-context",
        "small-window",
        "bad-range",
    ],
)
def test_main_bad_input_exits_2_with_one_line(
    tmp_path: Path, capsys, argv: list[str], message: str
) -> None:
    file = _module_file(tmp_path)
    argv = [a.format(tmp=tmp_path, file=file) for a in argv]

    assert main(argv) == 2

    err = capsys.readouterr().err
    assert message in err
    assert err.count("\n") == 1


@pytest.mark.parametrize(
    "source", ["print(1)\nx = 2 + 3\n", '"""Пакет."""\n', "LIMIT = 10\n"]
)
def test_main_empty_outline_is_a_warning(tmp_path: Path, capsys, source: str) -> None:
    """Пустой контур - и у нормальных файлов (docstring, константы): код 0."""
    script = tmp_path / "script.py"
    script.write_text(source, encoding="utf-8")

    assert main(["--file", str(script)]) == 0
    out = capsys.readouterr().out
    assert "предупреждение: пустой контур" in out
    assert "проблемы: -" in out


def test_main_json_write_error_exits_2(tmp_path: Path, capsys) -> None:
    script = tmp_path / "m.py"
    script.write_text("def f():\n    return 1\n", encoding="utf-8")

    code = main(["--file", str(script), "--json", str(tmp_path / "absent" / "x.json")])

    assert code == 2
    err = capsys.readouterr().err
    assert "результат не записан" in err
    assert "Traceback" not in err


def test_main_large_python_file_does_not_crash(tmp_path: Path) -> None:
    """Файл на 300 функций разбирается без segfault (#226); в дочернем процессе,
    как в `tests/sandbox/test_sandbox.py`: падение убило бы весь прогон pytest."""
    (tmp_path / "scripts").mkdir()
    big = tmp_path / "scripts" / "deploy_report.py"
    big.write_text(
        "".join(f"def f{i}():\n    return {i}\n\n" for i in range(300)),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "faulthandler",
            "-m",
            "tests.sandbox.outline",
            "--file",
            str(big),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "контур: определений 300, импортов 0" in result.stdout


def test_main_prints_every_status_line(tmp_path: Path, monkeypatch, capsys) -> None:
    """Бюджет, пропуск, фильтр, пустой файл и не вошедший дифф - каждый своей строкой."""
    deep, body_line = _deep_source()
    huge_size = Settings.model_fields["ast_max_file_bytes"].default
    files = {
        "deep.py": deep,
        "vendor/x.py": "def f():\n    return 1\n",
        "empty.py": "",
        "huge.py": "x = 1\n" * (huge_size // 6 + 1),
        **{f"notes/f{n:02}.txt": f"строка {n}\n" * 20 for n in range(80)},
        # Hunk крупнее заполнителей: в остаток бюджета не влезает.
        "z_late.py": "def late():\n" + "    total = 1\n" * 60,
    }
    case = {
        "title": "Все статусы",
        # Длинный заголовок не влезает в долю контекста PR: запись omitted без пути.
        "pull_request": {"title": "заголовок " * 300, "description": "d"},
        "files": files,
        "lines": {"deep.py": str(body_line)},
    }
    (tmp_path / "case.yaml").write_text(
        yaml.safe_dump(case, allow_unicode=True), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    code = main(
        ["--case-file", "case.yaml", "--context-max-tokens", "1024"]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert f"deep.py  python  изменены строки {body_line}\n" in out
    assert "  отброшено бюджетом: scopes\n" in out
    assert "vendor/x.py  отфильтрован\n" in out
    assert "empty.py  python  нет hunk'ов" in out
    assert "huge.py  python  изменены строки " in out
    assert "  AST пропущен: too_large" in out
    assert "z_late.py  python  hunk'и не вошли в пакет по бюджету" in out
    # Запись без пути - только часть, с путём - путь, строка hunk'а и часть.
    assert "отброшено по бюджету: pr_title, huge.py:1 hunk, " in out


def test_main_payload_with_json_keeps_stdout_json(tmp_path: Path, capsys) -> None:
    out = tmp_path / "out.json"

    assert (
        main(["--file", str(_module_file(tmp_path)), "--payload", "--json", str(out)])
        == 0
    )

    captured = capsys.readouterr()
    assert json.loads(captured.out)[0]["omitted_count"] == 0
    assert f"результат: {out}" in captured.err


class _BrokenExtractor:
    """Экстрактор, который падает на любом файле, - как упавший парсер."""

    def extract(self, source: str, language: Language) -> FileOutline:
        raise RuntimeError("парсер упал")


def test_recording_extractor_remembers_failures() -> None:
    recording = _RecordingExtractor(_BrokenExtractor())

    with pytest.raises(RuntimeError):
        recording.extract("x = 1\n", Language.PYTHON)

    assert recording.failed == {("x = 1\n", Language.PYTHON)}
    assert recording.outlines == {}


def test_run_outline_parse_error_survives_trimmed_omitted() -> None:
    """Запись `parse_error` вытеснена из урезанного `omitted`, а причина остаётся."""
    # Перечень - фильтр, а не обрезка: короткая запись влезла бы после длинных. Поэтому у
    # файла длинный путь, а мелкие записи отфильтрованных файлов выбирают бюджет до неё.
    app_path = "app/" + "deeply_nested_package/" * 12 + "app.py"
    vendor = {f"vendor/m{n:03}.py": "x = 1\n" for n in range(400)}
    pr = _pr("broken", {}, {app_path: "def f():\n    return 1\n", **vendor})

    [outline] = run_outline(
        [pr], SandboxConfig(context_max_tokens=1024), extractor_factory=_BrokenExtractor
    ).prs

    app = next(f for f in outline.files if f.path == app_path)
    assert (app.status, app.reason) == ("skipped", "parse_error")
    assert outline.omitted_count > len(outline.omitted)
    assert not any(e.get("reason") == "parse_error" for e in outline.omitted)


def test_report_prints_deleted_file(capsys) -> None:
    pr = _pr("gone", {"gone.py": "def g():\n    pass\n"}, {"gone.py": None})

    [outline] = run_outline([pr], SandboxConfig()).prs
    _print_pr(outline, [], full_outline=False)

    assert outline.files[0].status == "deleted"
    assert "gone.py  удалён\n" in capsys.readouterr().out


def test_main_diff_too_large_exits_1(tmp_path: Path, monkeypatch, capsys) -> None:
    """Дифф больше `DIFF_MAX_BYTES` - ошибка PR и код 1; лимит понижен переменной окружения."""
    monkeypatch.setenv("DIFF_MAX_BYTES", "100")

    assert main(["--file", str(_module_file(tmp_path))]) == 1

    assert "проблемы: дифф не получен: " in capsys.readouterr().out


# --- doctor и грамматики --------------------------------------------------

DOCTOR = REPO_ROOT / "scripts" / "sandbox-outline-doctor.sh"


def test_grammars_reject_tree_sitter_0_26_0(capsys) -> None:
    """0.26.0 роняет процесс на файлах длиннее 256 строк (#226) - ✗ и код 1."""

    def version(package: str) -> str:
        return "0.26.0" if package == "tree-sitter" else "0.25.0"

    assert grammars_main(version=version) == 1

    out = capsys.readouterr().out
    assert "✗ tree-sitter 0.26.0" in out
    assert "#226" in out


def test_grammars_missing_package_is_a_failure(capsys) -> None:
    def version(package: str) -> str:
        if package == "tree-sitter-go":
            raise PackageNotFoundError(package)
        return "0.25.2"

    assert grammars_main(version=version) == 1
    assert "✗ tree-sitter-go не установлен" in capsys.readouterr().out


def test_grammars_real_environment_passes(capsys) -> None:
    assert grammars_main() == 0

    out = capsys.readouterr().out
    assert "✗" not in out
    for language in ("python", "typescript", "tsx", "javascript", "go"):
        assert f"✓ разбор {language}" in out


def _doctor(*args: str, path: str | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    return subprocess.run(
        [str(DOCTOR), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_doctor_without_uv_fails_with_hint(tmp_path: Path) -> None:
    """В `PATH` - только то, что нужно скрипту, без `uv`: системный `/usr/bin` может
    его и содержать, если `uv` поставлен пакетным менеджером."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "git"):
        found = shutil.which(tool)
        assert found is not None, tool
        (bin_dir / tool).symlink_to(found)

    result = _doctor(path=str(bin_dir))

    assert result.returncode == 1, result.stdout
    assert "✗ uv не установлен" in result.stdout
    assert "astral.sh/uv" in result.stdout
    assert "ollama" not in result.stdout.lower()


def test_doctor_on_real_environment_passes() -> None:
    result = _doctor()

    assert result.returncode == 0, result.stdout
    assert "✓ uv " in result.stdout
    assert "Окружение готово" in result.stdout


def test_doctor_grammars_passes() -> None:
    result = _doctor("--grammars")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "✓ разбор go" in result.stdout


def test_grammars_missing_grammar_module_reports_instead_of_crashing(
    monkeypatch, capsys
) -> None:
    """Без модуля грамматики Go - строки версий и ✗ парсеров, а не traceback до `main`."""
    monkeypatch.setitem(sys.modules, "tree_sitter_go", None)
    monkeypatch.delitem(
        sys.modules, "app.infrastructure.parsing.tree_sitter_extractor", raising=False
    )

    assert grammars_main() == 1

    out = capsys.readouterr().out
    assert "✓ tree-sitter-python " in out
    assert "✗ парсеры tree-sitter не создаются" in out


def test_doctor_rejects_unknown_argument() -> None:
    result = _doctor("--bogus")

    assert result.returncode == 1
    assert "✗ неизвестный аргумент --bogus" in result.stdout


@pytest.mark.parametrize(
    "project_env", [None, "elsewhere/env"], ids=["default", "uv-env"]
)
def test_doctor_grammars_without_environment_creates_nothing(
    tmp_path: Path, project_env: str | None
) -> None:
    """Нет окружения - ✗ с подсказкой; doctor ничего не создаёт (ни .venv, ни Python).

    `UV_PROJECT_ENVIRONMENT` на пустой путь - то же: проверяется сам путь, а не
    наличие переменной."""
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\nrequires-python = ">=3.14"\n',
        encoding="utf-8",
    )
    (tmp_path / "tests" / "sandbox" / "outline").mkdir(parents=True)
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"}
    }
    if project_env is not None:
        env["UV_PROJECT_ENVIRONMENT"] = str(tmp_path / project_env)

    result = subprocess.run(
        [str(DOCTOR), "--grammars"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 1
    assert "запустите uv sync --all-extras" in result.stdout
    assert not (tmp_path / ".venv").exists()
    assert not (tmp_path / "elsewhere").exists()
