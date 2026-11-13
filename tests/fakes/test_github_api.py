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
