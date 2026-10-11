"""Сценарий сборки контекста на фейках VCS, кэша и парсера — без сети и tree-sitter."""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.application.context_assembly import AssembledContext, ContextLimits
from app.application.use_cases.build_review_context import build_review_context
from app.domain.entities import Definition, FileOutline, ReviewJob
from app.domain.enums import DefinitionKind, Language
from app.domain.vcs_errors import VcsError

from ..fakes.context import FakeCache, FakeExtractor
from ..fakes.webhooks import FakeVcs

NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
BASE, HEAD = "b" * 40, "a" * 40

JOB = ReviewJob(
    id=UUID(int=1),
    review_run_id=UUID(int=2),
    event_type="pull_request",
    action="synchronize",
    installation_id=77,
    repository_provider_id="1",
    repository_full_name="owner/repo",
    pull_request_number=5,
    head_sha=HEAD,
    base_sha=BASE,
)
LIMITS = ContextLimits(32_000, 4_000, 32_768, 60, ast_max_files=20, ast_max_file_bytes=1_000)

SOURCE = "class Repo:\n    def save(self):\n        return 2\n"
OUTLINE = FileOutline(
    definitions=(
        Definition(DefinitionKind.CLASS, "Repo", "class Repo", 1, 3),
        Definition(DefinitionKind.METHOD, "save", "def save(self)", 2, 3, "Repo"),
    ),
    imports=(),
)


def _file_diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n"
        "@@ -1,3 +1,3 @@\n class Repo:\n     def save(self):\n-        return 1\n+        return 2\n"
    )


def _build(vcs: FakeVcs, extractor: FakeExtractor | None = None, limits: ContextLimits = LIMITS) -> AssembledContext:
    return build_review_context(
        JOB,
        vcs=vcs,
        extractor=extractor or FakeExtractor(outlines={SOURCE: OUTLINE}),
        cache=FakeCache(),
        limits=limits,
        review_run_id=JOB.review_run_id,
        now=NOW,
        new_id=lambda: UUID(int=9),
    )


def test_diff_is_fetched_for_the_job_commit_pair() -> None:
    vcs = FakeVcs(diff=_file_diff("app/repo.py"), files={("app/repo.py", HEAD): SOURCE})
    _build(vcs)
    assert vcs.calls[0] == ("diff", "owner/repo", BASE, HEAD, 77)


def test_rules_at_base_and_sources_at_head() -> None:
    vcs = FakeVcs(diff=_file_diff("app/repo.py"), files={("app/repo.py", HEAD): SOURCE})
    _build(vcs)
    file_calls = [c for c in vcs.calls if c[0] == "file"]
    assert ("file", "owner/repo", ".review/rules.md", BASE, 77) in file_calls
    assert ("file", "owner/repo", "app/repo.py", HEAD, 77) in file_calls


def test_full_context_contains_ast_of_changed_file() -> None:
    vcs = FakeVcs(diff=_file_diff("app/repo.py"), files={("app/repo.py", HEAD): SOURCE})
    result = _build(vcs)
    assert result.payload.tiers == ("diff", "ast")
    assert "2-3 def save(self)" in result.payload.body["ast"][0]["scopes"]
    assert result.payload.body["rules"]["status"] == "absent"


def test_diff_failure_propagates() -> None:
    with pytest.raises(VcsError):
        _build(FakeVcs(error=VcsError("GitHub недоступен")))


def test_file_fetch_failure_degrades_only_that_file() -> None:
    diff = _file_diff("app/a.py") + _file_diff("app/b.py")
    vcs = FakeVcs(
        diff=diff,
        files={("app/b.py", HEAD): SOURCE},
        file_errors={"app/a.py": VcsError("503")},
    )
    body = _build(vcs).payload.body
    assert {"path": "app/a.py", "part": "ast", "reason": "unavailable"} in body["omitted"]
    assert [item["path"] for item in body["ast"]] == ["app/b.py"]
    assert {item["path"] for item in body["diff"]} == {"app/a.py", "app/b.py"}


def test_parser_failure_degrades_only_that_file() -> None:
    vcs = FakeVcs(diff=_file_diff("app/a.py"), files={("app/a.py", HEAD): SOURCE})
    body = _build(vcs, FakeExtractor(error=RuntimeError("segfault-ish"))).payload.body
    assert {"path": "app/a.py", "part": "ast", "reason": "parse_error"} in body["omitted"]
    assert body["diff"]


def test_files_beyond_limit_are_not_fetched() -> None:
    diff = "".join(_file_diff(f"app/f{i}.py") for i in range(5))
    vcs = FakeVcs(diff=diff, files={(f"app/f{i}.py", HEAD): SOURCE for i in range(5)})
    limits = ContextLimits(32_000, 4_000, 32_768, 60, ast_max_files=2, ast_max_file_bytes=1_000)
    body = _build(vcs, limits=limits).payload.body
    fetched = [c[2] for c in vcs.calls if c[0] == "file" and c[3] == HEAD]
    assert fetched == ["app/f0.py", "app/f1.py"]
    assert [e["path"] for e in body["omitted"] if e["reason"] == "file_limit"] == [
        "app/f2.py",
        "app/f3.py",
        "app/f4.py",
    ]


def test_too_large_file_is_not_parsed() -> None:
    big = "x = 1\n" * 500
    vcs = FakeVcs(diff=_file_diff("app/a.py"), files={("app/a.py", HEAD): big})
    extractor = FakeExtractor()
    body = _build(vcs, extractor).payload.body
    assert extractor.calls == []
    assert {"path": "app/a.py", "part": "ast", "reason": "too_large"} in body["omitted"]


def test_unsupported_language_is_not_fetched() -> None:
    vcs = FakeVcs(diff=_file_diff("docs/guide.rst"))
    body = _build(vcs).payload.body
    assert [c for c in vcs.calls if c[0] == "file" and c[3] == HEAD] == []
    assert body["diff"]
    assert body["ast"] == []


def test_parser_receives_language_from_extension() -> None:
    vcs = FakeVcs(diff=_file_diff("web/app.tsx"), files={("web/app.tsx", HEAD): SOURCE})
    extractor = FakeExtractor()
    _build(vcs, extractor)
    assert extractor.calls == [(SOURCE, Language.TSX)]


def test_deleted_file_gets_no_ast() -> None:
    diff = (
        "diff --git a/app/gone.py b/app/gone.py\ndeleted file mode 100644\n"
        "--- a/app/gone.py\n+++ /dev/null\n@@ -1,2 +0,0 @@\n-a\n-b\n"
    )
    vcs = FakeVcs(diff=diff)
    _build(vcs)
    assert [c for c in vcs.calls if c[0] == "file" and c[2] == "app/gone.py"] == []
