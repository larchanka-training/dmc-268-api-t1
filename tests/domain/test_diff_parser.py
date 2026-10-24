"""Разбор unified diff и фильтрация шума — всё на литеральных диффах.

Ни базы, ни сети, ни моков: диффы ниже записаны как есть, ожидаемые номера
строк взяты из заголовков `@@` вручную, а не пересчитаны тем же способом,
что и в парсере.
"""

from dataclasses import replace

import pytest

from app.domain.diff_parser import filter_diff_files, is_filterable, parse_diff
from app.domain.entities import Hunk, ParsedFile

MULTI_FILE_DIFF = """\
diff --git a/app/main.py b/app/main.py
index 3f7d2e1..9c4a8b2 100644
--- a/app/main.py
+++ b/app/main.py
@@ -1,4 +1,4 @@
 line one
-old two
+new two
 line three
 line four
diff --git a/docs/README.md b/docs/README.md
index 51b8afe..e04d2b8 100644
--- a/docs/README.md
+++ b/docs/README.md
@@ -10,3 +10,4 @@
 context around
-removed line
+added line
+another added line
 context after
"""

TWO_HUNKS_DIFF = """\
diff --git a/app/service.py b/app/service.py
--- a/app/service.py
+++ b/app/service.py
@@ -5,3 +5,3 @@
 keep
-before
+after
 keep
@@ -30 +31 @@
-solo
+solo new
"""

NEW_FILE_DIFF = """\
diff --git a/app/new_module.py b/app/new_module.py
new file mode 100644
index 0000000..d95b1c2
--- /dev/null
+++ b/app/new_module.py
@@ -0,0 +1,2 @@
+first line
+second line
"""

DELETED_FILE_DIFF = """\
diff --git a/app/gone.py b/app/gone.py
deleted file mode 100644
index d95b1c2..0000000
--- a/app/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-first line
-second line
"""

RENAME_WITH_EDITS_DIFF = """\
diff --git a/app/old_name.py b/app/new_name.py
similarity index 90%
rename from app/old_name.py
rename to app/new_name.py
index 1a2b3c4..4d5e6f7 100644
--- a/app/old_name.py
+++ b/app/new_name.py
@@ -2,3 +2,3 @@
 context
-old line
+new line
 context
"""

PURE_RENAME_DIFF = """\
diff --git a/app/before.py b/app/after.py
similarity index 100%
rename from app/before.py
rename to app/after.py
"""

BINARY_DIFF = """\
diff --git a/assets/logo.png b/assets/logo.png
index 3f2b1c0..8e9f0a1 100644
Binary files a/assets/logo.png and b/assets/logo.png differ
"""

GIT_BINARY_DIFF = """\
diff --git a/assets/icon.png b/assets/icon.png
index 1a2b3c4..5d6e7f8 100644
GIT binary patch
literal 512
zcmVvZ
"""

BINARY_BETWEEN_TEXT_DIFF = """\
diff --git a/app/first.py b/app/first.py
--- a/app/first.py
+++ b/app/first.py
@@ -3,3 +3,3 @@
  context
-first
+second
  context
diff --git a/assets/photo.png b/assets/photo.png
index 3f2b1c0..8e9f0a1 100644
Binary files a/assets/photo.png and b/assets/photo.png differ
diff --git a/app/third.py b/app/third.py
--- a/app/third.py
+++ b/app/third.py
@@ -7 +7 @@
- old line
+ new line
"""

NO_NEWLINE_DIFF = """\
diff --git a/notes.txt b/notes.txt
--- a/notes.txt
+++ b/notes.txt
@@ -1,2 +1,2 @@
 first
-last line
\\ No newline at end of file
+last line changed
\\ No newline at end of file
"""

NOISY_DIFF = """\
diff --git a/package-lock.json b/package-lock.json
--- a/package-lock.json
+++ b/package-lock.json
@@ -1,3 +1,3 @@
 {
-  "lockfileVersion": 2
+  "lockfileVersion": 3
 }
diff --git a/static/bundle.min.js b/static/bundle.min.js
--- a/static/bundle.min.js
+++ b/static/bundle.min.js
@@ -1 +1 @@
-!function(){}
+!function(){return 1}
diff --git a/app/main.py b/app/main.py
--- a/app/main.py
+++ b/app/main.py
@@ -1,2 +1,3 @@
 def main():
-    print("hi")
+    print("hello")
+    print("world")
"""


def test_multi_file_diff_is_split_into_files() -> None:
    files = parse_diff(MULTI_FILE_DIFF)
    assert [f.file_path for f in files] == ["app/main.py", "docs/README.md"]


def test_first_file_is_parsed_exactly() -> None:
    assert parse_diff(MULTI_FILE_DIFF)[0] == ParsedFile(
        file_path="app/main.py",
        hunks=(
            Hunk(
                file_path="app/main.py",
                old_start=1,
                old_count=4,
                new_start=1,
                new_count=4,
                changed_new_lines=frozenset({2}),
                changed_old_lines=frozenset({2}),
                text="@@ -1,4 +1,4 @@\n line one\n-old two\n+new two\n line three\n line four",
            ),
        ),
    )


def test_each_file_numbers_come_from_its_own_header() -> None:
    hunk = parse_diff(MULTI_FILE_DIFF)[1].hunks[0]
    assert (hunk.old_start, hunk.old_count, hunk.new_start, hunk.new_count) == (
        10,
        3,
        10,
        4,
    )


def test_context_lines_shift_counters_but_stay_out_of_changed_sets() -> None:
    """Контекст на 10-й и 13-й строках: без сдвига изменения были бы на 10 и 11."""
    hunk = parse_diff(MULTI_FILE_DIFF)[1].hunks[0]
    assert hunk.changed_new_lines == frozenset({11, 12})
    assert hunk.changed_old_lines == frozenset({11})


def test_second_hunk_restarts_numbering_from_its_header() -> None:
    """`@@ -30 +31 @@` без счётчиков: по одной строке с каждой стороны, не ноль."""
    hunks = parse_diff(TWO_HUNKS_DIFF)[0].hunks
    assert len(hunks) == 2
    assert hunks[1] == Hunk(
        file_path="app/service.py",
        old_start=30,
        old_count=1,
        new_start=31,
        new_count=1,
        changed_new_lines=frozenset({31}),
        changed_old_lines=frozenset({30}),
        text="@@ -30 +31 @@\n-solo\n+solo new",
    )


def test_new_file_has_no_old_side() -> None:
    parsed = parse_diff(NEW_FILE_DIFF)[0]
    assert parsed.old_path is None
    hunk = parsed.hunks[0]
    assert hunk.old_start == 0
    assert hunk.old_count == 0
    assert hunk.changed_old_lines == frozenset()
    assert hunk.changed_new_lines == frozenset({1, 2})


def test_deleted_file_keeps_its_path() -> None:
    parsed = parse_diff(DELETED_FILE_DIFF)[0]
    assert parsed.file_path == "app/gone.py"
    assert parsed.is_deleted
    assert parsed.old_path is None
    assert parsed.hunks[0].changed_old_lines == frozenset({1, 2})
    assert parsed.hunks[0].changed_new_lines == frozenset()


def test_edited_and_new_files_are_not_deleted() -> None:
    assert not parse_diff(MULTI_FILE_DIFF)[0].is_deleted
    assert not parse_diff(NEW_FILE_DIFF)[0].is_deleted


def test_hunk_text_stops_at_the_next_hunk() -> None:
    """Текст hunk'а — его заголовок и тело, без соседнего hunk'а и шапки файла."""
    first, second = parse_diff(TWO_HUNKS_DIFF)[0].hunks
    assert first.text == "@@ -5,3 +5,3 @@\n keep\n-before\n+after\n keep"
    assert second.text.startswith("@@ -30 +31 @@")


def test_rename_with_edits_sets_both_paths() -> None:
    parsed = parse_diff(RENAME_WITH_EDITS_DIFF)[0]
    assert parsed.old_path == "app/old_name.py"
    assert parsed.file_path == "app/new_name.py"
    assert parsed.hunks[0].file_path == "app/new_name.py"


def test_pure_rename_yields_file_without_hunks() -> None:
    assert parse_diff(PURE_RENAME_DIFF)[0] == ParsedFile(
        file_path="app/after.py", old_path="app/before.py", hunks=()
    )


def test_binary_files_marker_sets_is_binary() -> None:
    parsed = parse_diff(BINARY_DIFF)[0]
    assert parsed.is_binary
    assert parsed.hunks == ()


def test_git_binary_patch_sets_is_binary() -> None:
    parsed = parse_diff(GIT_BINARY_DIFF)[0]
    assert parsed.is_binary
    assert parsed.hunks == ()


def test_binary_between_text_files_does_not_stop_parsing() -> None:
    """Бинарный файл в середине диффа не прерывает разбор соседей.

    Сам он помечен `is_binary` с пустыми hunk'ами, а текстовые файлы до и
    после разобраны с точными номерами строк из своих заголовков `@@`.
    """
    files = parse_diff(BINARY_BETWEEN_TEXT_DIFF)
    assert [f.file_path for f in files] == [
        "app/first.py",
        "assets/photo.png",
        "app/third.py",
    ]

    # Номера строк здесь важнее текста: текст hunk'а проверен отдельно.
    first, binary, third = (
        replace(f, hunks=tuple(replace(h, text="") for h in f.hunks)) for f in files
    )
    assert binary.is_binary is True
    assert binary.hunks == ()

    assert first == ParsedFile(
        file_path="app/first.py",
        hunks=(
            Hunk(
                file_path="app/first.py",
                old_start=3,
                old_count=3,
                new_start=3,
                new_count=3,
                changed_old_lines=frozenset({4}),
                changed_new_lines=frozenset({4}),
            ),
        ),
    )
    assert third == ParsedFile(
        file_path="app/third.py",
        hunks=(
            Hunk(
                file_path="app/third.py",
                old_start=7,
                old_count=1,
                new_start=7,
                new_count=1,
                changed_old_lines=frozenset({7}),
                changed_new_lines=frozenset({7}),
            ),
        ),
    )


def test_no_newline_marker_is_not_a_file_line() -> None:
    """Маркер не строка файла: изменения остаются на строке 2, а не съезжают."""
    hunk = parse_diff(NO_NEWLINE_DIFF)[0].hunks[0]
    assert hunk.changed_old_lines == frozenset({2})
    assert hunk.changed_new_lines == frozenset({2})


def test_empty_diff_returns_no_files() -> None:
    assert parse_diff("") == []


def test_blank_diff_returns_no_files() -> None:
    assert parse_diff("\n  \n") == []


@pytest.mark.parametrize(
    "path",
    [
        "package-lock.json",
        "npm-shrinkwrap.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "poetry.lock",
        "uv.lock",
        "Pipfile.lock",
        "composer.lock",
        "Cargo.lock",
        "Gemfile.lock",
        "go.sum",
    ],
)
def test_lock_files_are_filterable(path: str) -> None:
    assert is_filterable(path, is_binary=False)


@pytest.mark.parametrize(
    "path",
    ["static/bundle.min.js", "assets/styles.min.css", "app.min.js"],
)
def test_minified_assets_are_filterable(path: str) -> None:
    assert is_filterable(path, is_binary=False)


@pytest.mark.parametrize(
    "path",
    ["src/api.generated.ts", "gen/types.generated.js"],
)
def test_generated_files_are_filterable(path: str) -> None:
    assert is_filterable(path, is_binary=False)


@pytest.mark.parametrize("path", ["proto/service.pb.go", "pkg/api.pb.go"])
def test_protobuf_files_are_filterable(path: str) -> None:
    assert is_filterable(path, is_binary=False)


def test_binary_files_are_filterable() -> None:
    assert is_filterable("assets/logo.png", is_binary=True)
    assert is_filterable("app/main.py", is_binary=True)


@pytest.mark.parametrize(
    "path",
    [
        "vendor/github.com/pkg/x.go",
        "node_modules/react/index.js",
        "app/static/node_modules/foo.js",
        "python/vendor/lib.py",
    ],
)
def test_vendor_directories_are_filterable(path: str) -> None:
    assert is_filterable(path, is_binary=False)


@pytest.mark.parametrize(
    "path",
    [
        "app/main.py",
        "src/components/Button.tsx",
        "docs/README.md",
        "app/lock.py",
        "app/vendor.py",
    ],
)
def test_regular_sources_pass_the_filter(path: str) -> None:
    assert not is_filterable(path, is_binary=False)


def test_filter_diff_files_keeps_reviewable_files_in_order() -> None:
    kept = filter_diff_files(parse_diff(NOISY_DIFF))
    assert [f.file_path for f in kept] == ["app/main.py"]
    assert kept[0].hunks[0].changed_new_lines == frozenset({2, 3})


def test_filter_diff_files_on_empty_input() -> None:
    assert filter_diff_files([]) == []


# Вывод `git diff` как есть: с `core.quotePath` пути с не-ASCII, `"` и
# управляющими символами идут в кавычках, байты UTF-8 — восьмеричными escape.
QUOTED_PATHS_DIFF = r'''diff --git "a/\320\277\321\200\320\270\320\262\320\265\321\202.py" "b/\320\277\321\200\320\270\320\262\320\265\321\202.py"
index 7d4290a..407de30 100644
--- "a/\320\277\321\200\320\270\320\262\320\265\321\202.py"
+++ "b/\320\277\321\200\320\270\320\262\320\265\321\202.py"
@@ -1 +1 @@
-x = 1
+x = 2
diff --git "a/tab\"quote.py" "b/tab\"quote.py"
index bca70f3..e556b83 100644
--- "a/tab\"quote.py"
+++ "b/tab\"quote.py"
@@ -1 +1 @@
-q
+w
diff --git "a/\320\275\320\276\320\262\321\213\320\271 space.py" "b/\320\275\320\276\320\262\321\213\320\271 space.py"
new file mode 100644
index 0000000..6178079
--- /dev/null
+++ "b/\320\275\320\276\320\262\321\213\320\271 space.py"	
@@ -0,0 +1 @@
+b
'''


def test_quoted_non_ascii_path_is_decoded() -> None:
    edited, quote, new = parse_diff(QUOTED_PATHS_DIFF)
    assert edited.file_path == "привет.py"
    assert edited.hunks[0].file_path == "привет.py"
    # Одинаковые пути в кавычках — не переименование.
    assert edited.old_path is None
    assert quote.file_path == 'tab"quote.py'
    assert quote.old_path is None
    assert new.file_path == "новый space.py"


def test_quoted_rename_is_decoded() -> None:
    diff = (
        'diff --git "a/\\320\\260.py" "b/\\320\\261.py"\n'
        "similarity index 90%\n"
        'rename from "\\320\\260.py"\n'
        'rename to "\\320\\261.py"\n'
    )
    (parsed,) = parse_diff(diff)
    assert (parsed.file_path, parsed.old_path) == ("б.py", "а.py")


def test_quoted_header_is_the_fallback_without_markers() -> None:
    """Чистое переименование без `---`/`+++`: пути берутся из `diff --git`."""
    diff = 'diff --git a/plain.py "b/\\320\\261.py"\nsimilarity index 100%\n'
    (parsed,) = parse_diff(diff)
    assert (parsed.file_path, parsed.old_path) == ("б.py", "plain.py")


def test_unquoted_paths_with_trailing_tab_are_unchanged() -> None:
    diff = "diff --git a/with space.py b/with space.py\n--- a/with space.py\t\n+++ b/with space.py\t\n@@ -1 +1 @@\n-a\n+b\n"
    (parsed,) = parse_diff(diff)
    assert (parsed.file_path, parsed.old_path) == ("with space.py", None)


# Git и GitHub делят дифф на строки только по `\n`. Остальные разделители
# `str.splitlines()` — часть содержимого строки кода: они не сдвигают номера
# строк и не превращают хвост строки в заголовок файла или hunk'а.
FOREIGN_LINE_SEPARATORS = ["\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", " ", " ", "\r"]


def _one_file_diff(*body: str) -> str:
    header = "diff --git a/app/x.py b/app/x.py\n--- a/app/x.py\n+++ b/app/x.py\n"
    return header + "\n".join(body) + "\n"


@pytest.mark.parametrize("separator", FOREIGN_LINE_SEPARATORS, ids=repr)
def test_foreign_separator_inside_line_keeps_line_numbers(separator: str) -> None:
    raw = _one_file_diff("@@ -1,2 +1,3 @@", " keep", f"+b{separator}c", "+d", " keep")

    [parsed] = parse_diff(raw)

    [hunk] = parsed.hunks
    assert hunk.changed_new_lines == frozenset({2, 3})
    assert hunk.text == f"@@ -1,2 +1,3 @@\n keep\n+b{separator}c\n+d\n keep"


@pytest.mark.parametrize("separator", FOREIGN_LINE_SEPARATORS, ids=repr)
def test_file_header_after_foreign_separator_is_not_a_file(separator: str) -> None:
    raw = _one_file_diff("@@ -0,0 +1,2 @@", f"+y{separator}diff --git a/evil.py b/evil.py", "+z")

    files = parse_diff(raw)

    assert [f.file_path for f in files] == ["app/x.py"]
    assert files[0].hunks[0].changed_new_lines == frozenset({1, 2})


@pytest.mark.parametrize("separator", FOREIGN_LINE_SEPARATORS, ids=repr)
def test_hunk_header_after_foreign_separator_is_not_a_hunk(separator: str) -> None:
    raw = _one_file_diff("@@ -0,0 +1,2 @@", f"+z{separator}@@ -100 +100 @@", "+w")

    [parsed] = parse_diff(raw)

    [hunk] = parsed.hunks
    assert (hunk.new_start, hunk.changed_new_lines) == (1, frozenset({1, 2}))


def test_crlf_diff_parses_as_before() -> None:
    # Ожидаемое снято с парсера до перехода на деление по `\n`: `\r` перед
    # `\n` в строку не попадает — ни в заголовках, ни в теле hunk'а.
    raw = (
        "diff --git a/w.py b/w.py\r\n--- a/w.py\r\n+++ b/w.py\r\n"
        "@@ -1,3 +1,3 @@\r\n keep\r\n-old\r\n+new\r\n keep\r\n"
    )

    assert parse_diff(raw) == [
        ParsedFile(
            file_path="w.py",
            old_path=None,
            is_binary=False,
            is_deleted=False,
            hunks=(
                Hunk(
                    file_path="w.py",
                    old_start=1,
                    old_count=3,
                    new_start=1,
                    new_count=3,
                    changed_new_lines=frozenset({2}),
                    changed_old_lines=frozenset({2}),
                    text="@@ -1,3 +1,3 @@\n keep\n-old\n+new\n keep",
                ),
            ),
        )
    ]
