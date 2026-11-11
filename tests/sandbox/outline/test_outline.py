"""Песочница tree-sitter: входы (`--file`/`--lines`, ключ `lines:` кейса)."""

import dataclasses
import json
import random
import subprocess
import sys
from pathlib import Path

import pytest

from app.domain.code_context import touched_lines
from app.domain.diff_parser import parse_diff
from app.infrastructure.parsing.tree_sitter_extractor import (
    TreeSitterDefinitionExtractor,
)

from ...fakes.github_api import file_diff
from ..inputs import InputError, PrInput, load_case_file
from ..worker import SandboxConfig
from .__main__ import _problems, main
from .runner import FullOutline, run_outline
from .sources import (
    OutlinePr,
    from_file,
    load_outline_case,
    parse_lines,
    touched_diff,
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

    # 123-124 и 131-132 - одинаковые пары пустых строк: дифф по тексту сдвинул бы
    # вставку на 125-132.
    pr = from_file(tmp_path / "big.py", "123-130")

    assert _touched(pr, "big.py") == set(range(123, 131))


def test_parse_lines_names_reversed_range() -> None:
    with pytest.raises(InputError, match="перевёрнут"):
        parse_lines("10-5", 20)


# Хвост или промежуток, совпадающий с концом диапазона: дифф по тексту
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


def _hunks(diff: str) -> list[str]:
    """Заголовки hunk'ов и `+`-строки диффа, без заголовков файла."""
    return [
        line
        for line in diff.splitlines()
        if line.startswith("@@") or (line.startswith("+") and not line.startswith("+++"))
    ]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        # Одна строка в середине: по 3 строки контекста с каждой стороны.
        ("5", ["@@ -2,6 +2,7 @@", "+l5"]),
        # Первая строка: контекст только снизу.
        ("1", ["@@ -1,3 +1,4 @@", "+l1"]),
        # Последняя строка: контекст только сверху.
        ("10", ["@@ -7,3 +7,4 @@", "+l10"]),
        # Диапазоны ближе 7 строк - один hunk.
        ("2, 8", ["@@ -1,8 +1,10 @@", "+l2", "+l8"]),
        # Дальше - два hunk'а; старая сторона второго сдвинута на добавленные строки.
        ("1-2, 10", ["@@ -1,3 +1,5 @@", "+l1", "+l2", "@@ -5,3 +7,4 @@", "+l10"]),
        # Весь файл: старой стороны нет - `-0,0`, как у нового файла.
        ("1-10", ["@@ -0,0 +1,10 @@", *(f"+l{n}" for n in range(1, 11))]),
    ],
)
def test_touched_diff_builds_hunks_by_line_numbers(spec: str, expected: list[str]) -> None:
    head = "".join(f"l{n}\n" for n in range(1, 11))

    diff = touched_diff("m.py", head, parse_lines(spec, 10))

    assert diff.startswith("diff --git a/m.py b/m.py\n--- a/m.py\n+++ b/m.py\n")
    assert _hunks(diff) == expected


def test_touched_diff_without_trailing_newline() -> None:
    diff = touched_diff("m.py", "a\nb", frozenset({2}))

    assert _hunks(diff) == ["@@ -1 +1,2 @@", "+b"]
    assert diff.endswith("+b\n")


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
    [file] = run_outline([pr], SandboxConfig())[0].files
    assert (file.path, file.coverage) == (rel, "reviewed")


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

    # База - head без затронутых строк: дифф её только дополняет.
    assert pr.base_files == {"a.py": "x = 1\nz = 3\n"}
    assert _touched(pr, "a.py") == {2}
    assert _touched(pr, "b.md") == {1}


def test_load_outline_case_reads_the_file_once(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "case.yaml"
    path.write_text(CASE + "lines:\n  a.py: 2\n", encoding="utf-8")
    reads: list[str] = []
    real = Path.read_text

    def read_text(self: Path, *args, **kwargs) -> str:
        reads.append(self.name)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_text)

    load_outline_case(path)

    assert reads == ["case.yaml"]


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


EXAMPLE = Path(__file__).parent / "examples" / "outline-case.yaml"
REPO_ROOT = Path(__file__).parents[3]
# Файл больше порога целого файла: AST для него выбирается.
BIG = REPO_ROOT / "app" / "infrastructure" / "parsing" / "tree_sitter_extractor.py"


def test_example_case_loads_in_worker_sandbox_too() -> None:
    """Ключ `lines:` не мешает песочнице воркера: `load_case_file` его игнорирует."""
    pr = load_case_file(EXAMPLE)

    assert pr.base_files == {}
    assert len(pr.head_files) == 8


def test_run_outline_report_is_the_payload(monkeypatch) -> None:
    """Отчёт по файлам - ровно разложенные по путям `ast`, `omitted` и охват пакета."""
    monkeypatch.chdir(REPO_ROOT)
    big = from_file(BIG, "72-95")

    prs = run_outline([load_outline_case(EXAMPLE), big], SandboxConfig())

    for pr in prs:
        assert pr.error is None
        # Каждый элемент `ast` и каждая запись `omitted` с путём - ровно у одного файла.
        assert sorted(map(repr, (i for f in pr.files for i in f.ast))) == sorted(
            map(repr, pr.ast)
        )
        assert sum(len(f.omitted) for f in pr.files) == sum(
            1 for e in pr.omitted if "path" in e
        )
        assert all(f.coverage == "reviewed" for f in pr.files)
    example, single = prs
    files = {f.path: f for f in example.files}
    # Маленькие файлы мок-PR вошли в дифф целиком, AST в пакете - только у файла больше 8 КБ.
    assert {"whole_file", "ast"} <= set(example.tiers)
    assert {item["path"] for item in example.ast} == {"app/orders/service.py"}
    service = files["app/orders/service.py"]
    assert {part for item in service.ast for part in ("scopes", "imports") if item.get(part)} == {
        "scopes",
        "imports",
    }
    assert files["docs/orders.md"].language is None
    # Большой файл: в пакет - класс и метод вокруг правки, текст - как видит модель.
    [file] = single.files
    parts = {part: item[part] for item in file.ast for part in ("scopes", "imports") if part in item}
    assert set(parts) == {"scopes", "imports"}
    assert parts["scopes"].startswith("<<<untrusted kind=scopes id=")
    assert "class TreeSitterDefinitionExtractor" in parts["scopes"]
    assert file.outline is None


def test_run_outline_full_outline_is_the_whole_file() -> None:
    source = "import os\n\n\ndef f() -> str:\n    return os.sep\n\n\ndef g() -> int:\n    return 1\n"

    [pr] = run_outline([_pr("full", {}, {"m.py": source})], SandboxConfig(), full_outline=True)

    [file] = pr.files
    assert file.outline == FullOutline(
        definitions=("4-5 def f() -> str", "8-9 def g() -> int"), imports=("import os",)
    )


def test_run_outline_full_outline_reports_parser_failure(monkeypatch) -> None:
    """Упавший разбор - имя типа в контуре, а не traceback; этап - `parse_error`."""

    def extract(self, source: str, language: object) -> None:
        raise RuntimeError("сломан")

    monkeypatch.setattr(TreeSitterDefinitionExtractor, "extract", extract)

    [pr] = run_outline([_pr("bad", {}, {"m.py": "x = 1\n"})], SandboxConfig(), full_outline=True)

    [file] = pr.files
    assert file.outline == FullOutline(definitions=(), imports=(), error="RuntimeError")
    assert _problems(pr) == ["m.py: parse_error"]


def test_run_outline_twins_beyond_file_limit_are_skipped(monkeypatch) -> None:
    """Одинаковый файл сверх `ast_max_files` - `file_limit`, а не контур близнеца."""
    monkeypatch.setenv("AST_MAX_FILES", "1")
    util = "def helper() -> int:\n    return 1\n"
    pr = _pr("twins", {}, {"pkg_a/util.py": util, "pkg_b/util.py": util})

    [outline] = run_outline([pr], SandboxConfig())

    files = {f.path: f for f in outline.files}
    assert files["pkg_a/util.py"].omitted == ()
    assert [(e["part"], e["reason"]) for e in files["pkg_b/util.py"].omitted] == [
        ("ast", "file_limit")
    ]


@pytest.mark.parametrize("reason", ["missing", "unavailable", "parse_error"])
def test_problems_flag_files_the_fake_github_should_have_served(reason: str) -> None:
    [outline] = run_outline([_pr("p", {}, {"a.py": "def f():\n    return 1\n"})], SandboxConfig())
    entry = {"path": "a.py", "part": "ast", "reason": reason}

    assert _problems(dataclasses.replace(outline, omitted=(entry,))) == [f"a.py: {reason}"]


# --- CLI ------------------------------------------------------------------


def _module_file(tmp_path: Path) -> Path:
    path = tmp_path / "m.py"
    path.write_text("import os\n\n\ndef f() -> str:\n    return os.sep\n", encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--file", "{tmp}/absent.py"], "нет"),
        (["--file", "{file}", "--lines", "9-1"], "строки"),
        (["--lines", "1"], "--lines"),
        (["--file", "{file}", "--file", "{file}", "--lines", "1"], "--lines"),
        (["--context-max-tokens", "100"], "context_max_tokens"),
        (["--from-git", "bad"], "BASE..HEAD"),
    ],
    ids=[
        "missing-file",
        "reversed-lines",
        "lines-without-file",
        "lines-with-two-files",
        "tiny-context",
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


def test_main_json_write_error_exits_2(tmp_path: Path, capsys) -> None:
    code = main(["--file", str(_module_file(tmp_path)), "--json", str(tmp_path / "absent" / "x.json")])

    assert code == 2
    err = capsys.readouterr().err
    assert "результат не записан" in err
    assert "Traceback" not in err


def test_main_json_is_the_run_result(tmp_path: Path) -> None:
    out = tmp_path / "r.json"

    assert main(["--file", str(_module_file(tmp_path)), "--json", str(out)]) == 0

    [pr] = json.loads(out.read_text(encoding="utf-8"))
    assert [f["path"] for f in pr["files"]] == [(tmp_path / "m.py").resolve().as_posix()]


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
            "--full-outline",
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
    assert "весь контур: определений 300, импортов 0" in result.stdout


def test_main_diff_too_large_exits_1(tmp_path: Path, monkeypatch, capsys) -> None:
    """Дифф больше `DIFF_MAX_BYTES` - ошибка PR и код 1; лимит понижен переменной окружения."""
    monkeypatch.setenv("DIFF_MAX_BYTES", "100")

    assert main(["--file", str(_module_file(tmp_path))]) == 1

    assert "проблемы: дифф не получен: " in capsys.readouterr().out
