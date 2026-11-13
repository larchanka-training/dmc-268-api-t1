"""Фейк GitHub: PR из готового диффа - через настоящие `GitHubAppAuth` и `GitHubVcsGateway`."""

import httpx
import pytest

from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth

from .github_api import (
    APP_ID,
    INSTALLATION_ID,
    REPO_FULL_NAME,
    FakeGitHub,
    app_private_key,
)

BASE, HEAD = "b" * 40, "c" * 40
MAX_BYTES = 1_000_000

# Изменённый файл с hunk'ом не с первой строки и добавленный файл целиком.
DIFF = """\
diff --git a/app/search.py b/app/search.py
index 1111111..2222222 100644
--- a/app/search.py
+++ b/app/search.py
@@ -8,3 +8,4 @@ def search(db, name):
     q = name.strip()
-    return db.query(q)
+    sql = f"SELECT * FROM users WHERE name = '{q}'"
+    return db.execute(sql)
 # конец
diff --git a/app/new.py b/app/new.py
new file mode 100644
--- /dev/null
+++ b/app/new.py
@@ -0,0 +1,2 @@
+def added():
+    return 1
"""
NEW_FILE = "def added():\n    return 1\n"


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub(REPO_FULL_NAME, INSTALLATION_ID, app_private_key())


def _gateway(github: FakeGitHub) -> GitHubVcsGateway:
    client = github.client()
    return GitHubVcsGateway(GitHubAppAuth(APP_ID, github.private_key, client), client)


def _token(github: FakeGitHub) -> str:
    client = github.client()
    return GitHubAppAuth(APP_ID, github.private_key, client).installation_token(INSTALLATION_ID)


def _review(github: FakeGitHub, path: str, line: int, side: str = "RIGHT") -> httpx.Response:
    return github.client().post(
        f"/repos/{REPO_FULL_NAME}/pulls/7/reviews",
        headers={"Authorization": f"Bearer {_token(github)}"},
        json={
            "commit_id": HEAD,
            "event": "COMMENT",
            "body": "сводка",
            "comments": [{"path": path, "line": line, "side": side, "body": "замечание"}],
        },
    )


def test_diff_is_served_verbatim_and_details_land_in_the_pull(github: FakeGitHub) -> None:
    github.open_pull_from_diff(
        7,
        base_sha=BASE,
        head_sha=HEAD,
        diff=DIFF,
        head_files={"app/new.py": NEW_FILE},
        title="feat: скачивание вложений",
        body="Описание PR с инструкцией ревьюеру.",
        head_ref="feat/attachments-download",
        base_ref="main",
    )
    vcs = _gateway(github)

    assert vcs.fetch_diff(REPO_FULL_NAME, BASE, HEAD, INSTALLATION_ID, max_bytes=MAX_BYTES) == DIFF
    meta = vcs.fetch_pr_metadata(REPO_FULL_NAME, 7, INSTALLATION_ID)
    assert (meta.title, meta.description, meta.source_branch, meta.target_branch) == (
        "feat: скачивание вложений",
        "Описание PR с инструкцией ревьюеру.",
        "feat/attachments-download",
        "main",
    )
    assert (meta.base_sha, meta.head_sha) == (BASE, HEAD)


def test_only_the_added_file_is_readable_at_head(github: FakeGitHub) -> None:
    github.open_pull_from_diff(
        7, base_sha=BASE, head_sha=HEAD, diff=DIFF, head_files={"app/new.py": NEW_FILE}
    )
    vcs = _gateway(github)

    def read(path: str, sha: str) -> str | None:
        return vcs.fetch_file(REPO_FULL_NAME, path, sha, INSTALLATION_ID, max_bytes=MAX_BYTES)

    assert read("app/new.py", HEAD) == NEW_FILE
    # Изменённого файла целиком нет: сборка контекста получит 404 и пропустит AST (`missing`).
    assert read("app/search.py", HEAD) is None
    assert read("app/search.py", BASE) is None


def test_inline_comment_is_checked_against_the_given_diff(github: FakeGitHub) -> None:
    github.open_pull_from_diff(7, base_sha=BASE, head_sha=HEAD, diff=DIFF)

    # Номера строк - из hunk'а `@@ -8,3 +8,4 @@`, а не с первой строки файла.
    assert _review(github, "app/search.py", 9).status_code == 200
    assert _review(github, "app/search.py", 9, side="LEFT").status_code == 200
    assert _review(github, "app/search.py", 2).status_code == 422
    assert _review(github, "app/new.py", 2).status_code == 200


def test_head_content_of_a_file_that_is_not_added_is_rejected(github: FakeGitHub) -> None:
    # Содержимое изменённого файла из hunk'ов не восстановить без сдвига строк.
    with pytest.raises(ValueError, match="app/search.py"):
        github.open_pull_from_diff(
            7, base_sha=BASE, head_sha=HEAD, diff=DIFF, head_files={"app/search.py": "x\n"}
        )


# Не-ASCII путь git пишет в кавычках с восьмеричными байтами UTF-8 (core.quotePath):
# hunk - из `tests/eval/dataset/TC-14-binary-rename-non-utf8/diff.patch`.
GUIDE = "docs/руководство.md"
_QUOTED_GUIDE = (
    r"docs/\321\200\321\203\320\272\320\276\320\262\320\276\320\264\321\201\321\202\320\262\320\276.md"
)
_QUOTED_OLD = r"docs/\321\201\321\202\320\260\321\200\320\276\320\265.md"
# Кавычку в имени git тоже экранирует: `\"`.
_QUOTED_NEW = r'docs/\320\275\320\276\320\262\320\276\320\265 \"\321\217\".md'
# Таб и обратная косая в имени - escape `\t` и `\\`.
_QUOTED_ESCAPES = r"docs/tab\t\\x.md"
# Заголовки `---`/`+++` - как их пишет `git diff`: у имени с пробелом git ставит в
# конце строки таб, и в кавычках - после закрывающей кавычки.
QUOTED_DIFF = f"""\
diff --git "a/{_QUOTED_GUIDE}" "b/{_QUOTED_GUIDE}"
index 3b1d8ba..50dea97 100644
--- "a/{_QUOTED_GUIDE}"
+++ "b/{_QUOTED_GUIDE}"
@@ -1,3 +1,9 @@
 # Руководство
 
 Запуск: `make run`.
+
+## Отчёты 📊
+
+Выгрузка — раз в сутки,в 03:00 UTC.
+Строка с разделителем строк Unicode.
+Пробел неразрывный и ё, Ё, ß.
\\ No newline at end of file
diff --git "a/{_QUOTED_OLD}" "b/{_QUOTED_OLD}"
deleted file mode 100644
--- "a/{_QUOTED_OLD}"
+++ /dev/null
@@ -1 +0,0 @@
-старое
diff --git "a/{_QUOTED_NEW}" "b/{_QUOTED_NEW}"
new file mode 100644
--- /dev/null
+++ "b/{_QUOTED_NEW}"\t
@@ -0,0 +1 @@
+новое
diff --git a/docs/a b.md b/docs/a b.md
new file mode 100644
--- /dev/null
+++ b/docs/a b.md\t
@@ -0,0 +1 @@
+пробел
diff --git "a/{_QUOTED_ESCAPES}" "b/{_QUOTED_ESCAPES}"
new file mode 100644
--- /dev/null
+++ "b/{_QUOTED_ESCAPES}"
@@ -0,0 +1 @@
+таб
"""
OLD_GUIDE = "docs/старое.md"
NEW_GUIDE = 'docs/новое "я".md'
SPACED = "docs/a b.md"
ESCAPED = "docs/tab\t\\x.md"


def test_quoted_non_ascii_paths_are_decoded_like_github_does(github: FakeGitHub) -> None:
    head_files = {NEW_GUIDE: "новое\n", SPACED: "пробел\n", ESCAPED: "таб\n"}
    github.open_pull_from_diff(7, base_sha=BASE, head_sha=HEAD, diff=QUOTED_DIFF, head_files=head_files)

    # GitHub принимает комментарий по настоящему пути, а не по экранированному.
    assert _review(github, GUIDE, 9).status_code == 200
    assert _review(github, GUIDE, 3, side="LEFT").status_code == 200
    assert _review(github, OLD_GUIDE, 1, side="LEFT").status_code == 200
    assert _review(github, NEW_GUIDE, 1).status_code == 200
    assert _review(github, SPACED, 1).status_code == 200
    assert _review(github, ESCAPED, 1).status_code == 200
    content = _gateway(github).fetch_file(
        REPO_FULL_NAME, NEW_GUIDE, HEAD, INSTALLATION_ID, max_bytes=MAX_BYTES
    )
    assert content == "новое\n"
