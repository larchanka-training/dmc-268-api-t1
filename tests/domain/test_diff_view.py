"""Представление диффа для кабинета — на литеральных диффах.

Ни базы, ни сети: дифф разбирает `parse_diff`, представление строит
`build_diff_view`. Номера строк в ожиданиях выписаны вручную из заголовков
`@@`, а не пересчитаны тем же способом, что и в коде.
"""

from app.domain.diff_parser import parse_diff
from app.domain.diff_view import DiffFileView, DiffHunkView, DiffLine, build_diff_view
from app.domain.enums import DiffFileStatus, DiffLineKind, Language

MODIFIED_DIFF = """\
diff --git a/app/main.py b/app/main.py
index 3f7d2e1..9c4a8b2 100644
--- a/app/main.py
+++ b/app/main.py
@@ -10,3 +10,3 @@ def main():
-    print("hi")
+    print("hello")
 return 0
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

TWO_HUNKS_DIFF = """\
diff --git a/app/service.py b/app/service.py
--- a/app/service.py
+++ b/app/service.py
@@ -5,2 +5,3 @@ class Service:
 keep
+inserted
 keep too
@@ -30 +31 @@
-solo
+solo new
"""


def _view(raw: str) -> tuple[DiffFileView, ...]:
    return build_diff_view(parse_diff(raw))


def test_hunk_lines_carry_kind_numbers_and_content_without_prefix() -> None:
    [file] = _view(MODIFIED_DIFF)

    assert file.hunks == (
        DiffHunkView(
            header="def main():",
            old_start=10,
            old_count=3,
            new_start=10,
            new_count=3,
            lines=(
                DiffLine(kind=DiffLineKind.REMOVED, old_line=10, new_line=None, content='    print("hi")'),
                DiffLine(kind=DiffLineKind.ADDED, old_line=None, new_line=10, content='    print("hello")'),
                DiffLine(kind=DiffLineKind.CONTEXT, old_line=11, new_line=11, content="return 0"),
            ),
        ),
    )


def test_no_newline_marker_is_skipped_and_does_not_shift_numbers() -> None:
    [file] = _view(NO_NEWLINE_DIFF)

    [hunk] = file.hunks
    assert hunk.lines == (
        DiffLine(kind=DiffLineKind.CONTEXT, old_line=1, new_line=1, content="first"),
        DiffLine(kind=DiffLineKind.REMOVED, old_line=2, new_line=None, content="last line"),
        DiffLine(kind=DiffLineKind.ADDED, old_line=None, new_line=2, content="last line changed"),
    )


def test_header_is_empty_without_text_after_second_marker() -> None:
    [file] = _view(TWO_HUNKS_DIFF)

    first, second = file.hunks
    assert first.header == "class Service:"
    assert second.header == ""


def test_each_hunk_numbers_lines_from_its_own_header() -> None:
    [file] = _view(TWO_HUNKS_DIFF)

    first, second = file.hunks
    assert first.lines == (
        DiffLine(kind=DiffLineKind.CONTEXT, old_line=5, new_line=5, content="keep"),
        DiffLine(kind=DiffLineKind.ADDED, old_line=None, new_line=6, content="inserted"),
        DiffLine(kind=DiffLineKind.CONTEXT, old_line=6, new_line=7, content="keep too"),
    )
    assert (second.old_start, second.old_count, second.new_start, second.new_count) == (30, 1, 31, 1)
    assert second.lines == (
        DiffLine(kind=DiffLineKind.REMOVED, old_line=30, new_line=None, content="solo"),
        DiffLine(kind=DiffLineKind.ADDED, old_line=None, new_line=31, content="solo new"),
    )


ADDED_AND_DELETED_DIFF = """\
diff --git a/app/new_module.py b/app/new_module.py
new file mode 100644
index 0000000..d95b1c2
--- /dev/null
+++ b/app/new_module.py
@@ -0,0 +1,2 @@
+first line
+second line
diff --git a/docs/gone.md b/docs/gone.md
deleted file mode 100644
index d95b1c2..0000000
--- a/docs/gone.md
+++ /dev/null
@@ -1,3 +0,0 @@
-one
-two
-three
"""

RENAME_DIFF = """\
diff --git a/old.py b/new.py
similarity index 90%
rename from old.py
rename to new.py
index 1a2b3c4..4d5e6f7 100644
--- a/old.py
+++ b/new.py
@@ -2,3 +2,3 @@
 context
-old line
+new line
 context
"""

BINARY_DIFF = """\
diff --git a/logo.png b/logo.png
index 3f2b1c0..8e9f0a1 100644
Binary files a/logo.png and b/logo.png differ
"""

LOCK_FILE_DIFF = """\
diff --git a/uv.lock b/uv.lock
--- a/uv.lock
+++ b/uv.lock
@@ -1 +1 @@
-version = 1
+version = 2
"""


def test_modified_file_has_status_language_and_counters() -> None:
    [file] = _view(MODIFIED_DIFF)

    assert file.path == "app/main.py"
    assert file.previous_path is None
    assert file.status is DiffFileStatus.MODIFIED
    assert file.is_binary is False
    assert file.language is Language.PYTHON
    assert (file.additions, file.deletions) == (1, 1)


def test_added_and_deleted_files_get_their_statuses_and_one_sided_counters() -> None:
    added, deleted = _view(ADDED_AND_DELETED_DIFF)

    assert (added.path, added.status, added.additions, added.deletions) == (
        "app/new_module.py",
        DiffFileStatus.ADDED,
        2,
        0,
    )
    assert (deleted.path, deleted.status, deleted.additions, deleted.deletions) == (
        "docs/gone.md",
        DiffFileStatus.DELETED,
        0,
        3,
    )


def test_language_is_none_without_grammar() -> None:
    _, deleted = _view(ADDED_AND_DELETED_DIFF)

    assert deleted.language is None


def test_rename_has_previous_path() -> None:
    [file] = _view(RENAME_DIFF)

    assert (file.path, file.previous_path, file.status) == ("new.py", "old.py", DiffFileStatus.RENAMED)


def test_binary_file_has_no_hunks_and_zero_counters() -> None:
    [file] = _view(BINARY_DIFF)

    assert file == DiffFileView(
        path="logo.png",
        previous_path=None,
        status=DiffFileStatus.MODIFIED,
        is_binary=True,
        language=None,
        additions=0,
        deletions=0,
        hunks=(),
    )


def test_lock_file_is_not_filtered() -> None:
    [file] = _view(LOCK_FILE_DIFF)

    assert file.path == "uv.lock"
    assert [line.content for line in file.hunks[0].lines] == ["version = 1", "version = 2"]


def test_files_keep_diff_order() -> None:
    assert [f.path for f in _view(ADDED_AND_DELETED_DIFF + LOCK_FILE_DIFF)] == [
        "app/new_module.py",
        "docs/gone.md",
        "uv.lock",
    ]


def test_empty_diff_gives_empty_tuple() -> None:
    assert build_diff_view(parse_diff("")) == ()
