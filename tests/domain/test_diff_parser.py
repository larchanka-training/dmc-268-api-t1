"""Разбор unified diff и фильтрация шума — всё на литеральных диффах.

Ни базы, ни сети, ни моков: диффы ниже записаны как есть, ожидаемые номера
строк взяты из заголовков `@@` вручную, а не пересчитаны тем же способом,
что и в парсере.
"""

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
    assert parsed.old_path is None
    assert parsed.hunks[0].changed_old_lines == frozenset({1, 2})
    assert parsed.hunks[0].changed_new_lines == frozenset()


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

    first, binary, third = files
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
