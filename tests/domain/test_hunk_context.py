"""Расширенные виды hunk'ов: окно вокруг изменения и файл целиком.

Ожидаемое берётся из `git diff` с большим контекстом для того же изменения, а не
пересчитывается тем же способом, что и в коде. Подпись функции после `@@` git берёт
из строки над началом hunk'а; расширенный вид её не пишет, поэтому сравнение - без неё.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

from app.domain.diff_parser import parse_diff
from app.domain.hunk_context import SURROUNDING_LINES, expand_hunks
from app.domain.secret_redaction import REDACTED

_HEADING = re.compile(r"^(@@ [^@]+ @@).*$", re.MULTILINE)
# Без глобального конфига пользователя: `diff.noprefix`, `diff.algorithm` меняют вывод.
_GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git_diff(tmp_path: Path, old: str, new: str, context: int) -> str:
    (tmp_path / "old").write_text(old)
    (tmp_path / "new").write_text(new)
    result = subprocess.run(
        ["git", "diff", "--no-index", "--no-color", "--no-ext-diff", f"-U{context}", "old", "new"],
        cwd=tmp_path,
        env=_GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    return result.stdout


def _hunk_texts(diff: str) -> tuple[str, ...]:
    (parsed,) = parse_diff(diff)
    return tuple(_HEADING.sub(r"\1", h.text) for h in parsed.hunks)


def _expanded(tmp_path: Path, old: str, new: str, radius: int) -> tuple[str, ...]:
    (parsed,) = parse_diff(_git_diff(tmp_path, old, new, 3))
    views = expand_hunks(parsed.hunks, new, radius)
    assert views is not None
    return views


def _lines(count: int) -> list[str]:
    return [f"line {n}" for n in range(1, count + 1)]


def _text(lines: list[str]) -> str:
    return "".join(f"{line}\n" for line in lines)


def _edit(lines: list[str], changes: dict[int, str | None]) -> list[str]:
    """Новая версия: строка с номером из `changes` заменяется, `None` - удаляется."""
    out: list[str] = []
    for number, line in enumerate(lines, start=1):
        replacement = changes.get(number, line)
        if replacement is not None:
            out.append(replacement)
    return out


@pytest.mark.parametrize(
    ("label", "changes"),
    [
        ("окна склеиваются", {40: "changed 40", 90: "changed 90"}),
        ("окна смыкаются: между изменениями ровно два радиуса", {40: "a", 101: "b"}),
        ("окна раздельно: на строку больше двух радиусов", {40: "a", 102: "b"}),
        ("начало файла", {2: "head"}),
        ("конец файла", {199: "tail"}),
        ("чистое удаление", {50: None, 51: None}),
        ("hunk'и, склеенные уже при трёх строках", {100: "x", 105: "y", 170: "z"}),
    ],
)
def test_window_matches_git_diff_u30(tmp_path: Path, label: str, changes: dict[int, str | None]) -> None:
    old = _lines(200)
    new = _edit(old, changes)

    expanded = _expanded(tmp_path, _text(old), _text(new), SURROUNDING_LINES)

    assert expanded == _hunk_texts(_git_diff(tmp_path, _text(old), _text(new), 30)), label


def test_pure_insertion_matches_git(tmp_path: Path) -> None:
    old = _lines(100)
    new = [*old[:50], "inserted a", "inserted b", *old[50:]]

    expanded = _expanded(tmp_path, _text(old), _text(new), SURROUNDING_LINES)

    assert expanded == _hunk_texts(_git_diff(tmp_path, _text(old), _text(new), 30))


def test_whole_file_is_one_view_of_every_line(tmp_path: Path) -> None:
    old = _lines(120)
    new = _edit(old, {10: "a", 110: "b"})

    (view,) = _expanded(tmp_path, _text(old), _text(new), len(new))

    assert (view,) == _hunk_texts(_git_diff(tmp_path, _text(old), _text(new), 1000))
    assert view.startswith("@@ -1,120 +1,120 @@\n")


def test_new_file_whole_view_is_its_only_hunk(tmp_path: Path) -> None:
    """Hunk нового файла уже покрывает его целиком: вид равен исходному, а не `None`."""
    new = _text(_lines(10))
    (parsed,) = parse_diff(
        "diff --git a/a.py b/a.py\nnew file mode 100644\n--- /dev/null\n+++ b/a.py\n"
        "@@ -0,0 +1,10 @@\n" + "".join(f"+{line}\n" for line in _lines(10))
    )

    assert expand_hunks(parsed.hunks, new, 11) == (parsed.hunks[0].text,)


def test_no_newline_marker_is_kept_inside_the_hunk(tmp_path: Path) -> None:
    old = _text(_lines(80)).removesuffix("\n")
    new = _text(_edit(_lines(80), {80: "last"})).removesuffix("\n")

    expanded = _expanded(tmp_path, old, new, SURROUNDING_LINES)

    assert expanded == _hunk_texts(_git_diff(tmp_path, old, new, 30))
    assert expanded[0].endswith("+last\n\\ No newline at end of file")


def test_no_newline_marker_is_added_to_the_trailing_context(tmp_path: Path) -> None:
    old = _text(_lines(80)).removesuffix("\n")
    new = _text(_edit(_lines(80), {70: "changed"})).removesuffix("\n")

    expanded = _expanded(tmp_path, old, new, SURROUNDING_LINES)

    assert expanded == _hunk_texts(_git_diff(tmp_path, old, new, 30))
    assert expanded[0].endswith(" line 80\n\\ No newline at end of file")


def test_mismatching_line_keeps_the_original_hunks() -> None:
    (parsed,) = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
        "@@ -2,3 +2,3 @@ def f():\n line 2\n-line 3\n+changed 3\n line 4\n"
    )
    # Файл на head_sha уже другой: добавленной строки в нём нет.
    head = _text(["line 1", "line 2", "other 3", "line 4", "line 5"])

    assert expand_hunks(parsed.hunks, head, SURROUNDING_LINES) is None


def test_hunk_past_the_end_of_the_file_keeps_the_original_hunks() -> None:
    (parsed,) = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -2,2 +2,2 @@\n line 2\n-x\n+line 3\n"
    )

    assert expand_hunks(parsed.hunks, "line 1\nline 2\n", SURROUNDING_LINES) is None


def test_hunk_without_changed_lines_keeps_the_original_hunks() -> None:
    """GitHub такой hunk не отдаёт, но сборка контекста не должна падать на нём целиком."""
    (parsed,) = parse_diff(
        "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,2 +1,2 @@\n line 1\n line 2\n"
    )

    assert expand_hunks(parsed.hunks, "line 1\nline 2\n", SURROUNDING_LINES) is None


TOKEN = "ghp_" + "a" * 36


def test_secret_in_context_line_is_redacted_but_not_in_changed_line(tmp_path: Path) -> None:
    old = _lines(100)
    old[39] = f"token = '{TOKEN}'"
    new = _edit(old, {50: f"added = '{TOKEN}'"})

    (view,) = _expanded(tmp_path, _text(old), _text(new), SURROUNDING_LINES)

    assert f" token = '{REDACTED}'\n" in view
    assert f"\n+added = '{TOKEN}'\n" in view


def test_multiline_key_in_context_stays_in_place(tmp_path: Path) -> None:
    """Ключ в окне - метка на каждой его строке: строки между ключом и изменением не сдвигаются."""
    key = ["-----BEGIN RSA PRIVATE KEY-----", "MIIEowIBAAKCAQEA" + "b" * 48, "c" * 64, "-----END RSA PRIVATE KEY-----"]
    old = [*_lines(30), "a=1", *key, "x = 1", "y = 2", "z = 3", "orig", *_lines(30)]
    new = _edit(old, {39: "CHANGED"})

    (view,) = _expanded(tmp_path, _text(old), _text(new), SURROUNDING_LINES)
    (git_view,) = _hunk_texts(_git_diff(tmp_path, _text(old), _text(new), 30))

    key_rows = {f" {line}" for line in key}
    got, want = view.split("\n"), git_view.split("\n")
    assert len(got) == len(want)
    for got_line, want_line in zip(got, want, strict=True):
        assert got_line == (f" {REDACTED}" if want_line in key_rows else want_line)
    assert "\n z = 3\n-orig\n+CHANGED\n" in view

