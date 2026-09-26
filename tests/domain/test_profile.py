"""Чистые функции профиля: чанкинг окон, отбор похожих, редакция секретов.

Ни базы, ни брокера, ни сети: всё, что эти функции решают, проверяется
литералами.
"""

import hashlib
from uuid import UUID

from app.domain.profile import (
    ProfileLimits,
    ScoredChunk,
    SimilarChunk,
    SurroundingWindow,
    build_chunks,
    pick_similar,
    redact,
)

LIMITS = ProfileLimits(max_chunk_bytes=64, retrieval_top_k=2, retrieval_byte_budget=100)
REPO_ID = UUID(int=10**24)
RUN_ID = UUID(int=10**25)


def window(
    n: int = 1,
    *,
    path: str = "app/main.py",
    start: int = 1,
    end: int = 10,
    text: str = "def handler():\n    return 1\n",
) -> SurroundingWindow:
    return SurroundingWindow(
        file_path=path,
        start_line=start,
        end_line=end,
        commit_sha=f"sha{n}",
        text=text,
    )


def scored(
    *,
    chunk_id: str = "c1",
    path: str = "lib/old.py",
    digest: str = "d1",
    body: str = "x = 1\n",
    distance: float = 0.5,
) -> ScoredChunk:
    return ScoredChunk(
        chunk_id=chunk_id,
        file_path=path,
        start_line=1,
        end_line=5,
        content_sha256=digest,
        body=body,
        distance=distance,
    )


# --- build_chunks -----------------------------------------------------------


def test_build_chunks_carries_path_bounds_commit_and_digest() -> None:
    text = "def handler():\n    return 1\n"
    drafts = build_chunks([window(text=text)], REPO_ID, RUN_ID, LIMITS)
    assert len(drafts) == 1
    draft = drafts[0]
    assert draft.file_path == "app/main.py"
    assert draft.start_line == 1
    assert draft.end_line == 10
    assert draft.commit_sha == "sha1"
    assert draft.repository_id == REPO_ID
    assert draft.review_run_id == RUN_ID
    assert draft.content_sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_build_chunks_orders_windows_as_given() -> None:
    drafts = build_chunks(
        [window(1, path="a.py"), window(2, path="b.py")], REPO_ID, RUN_ID, LIMITS
    )
    assert [d.file_path for d in drafts] == ["a.py", "b.py"]


def test_build_chunks_skips_windows_above_the_byte_limit() -> None:
    big = window(text="x" * (LIMITS.max_chunk_bytes + 1))
    small = window(text="y = 2\n")
    drafts = build_chunks([big, small], REPO_ID, RUN_ID, LIMITS)
    assert [d.file_path for d in drafts] == ["app/main.py"]


def test_repeated_window_yields_the_same_digest() -> None:
    first = build_chunks([window(text="same code")], REPO_ID, RUN_ID, LIMITS)[0]
    second = build_chunks([window(text="same code")], REPO_ID, RUN_ID, LIMITS)[0]
    assert first.content_sha256 == second.content_sha256


# --- redact -----------------------------------------------------------------


def test_redact_masks_token_prefixes() -> None:
    source = 'GITHUB_TOKEN = "ghp_0123456789abcdefghij"\n'
    redacted = redact(source)
    assert "0123456789abcdefghij" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_masks_an_aws_access_key() -> None:
    source = 'key = "AKIAIOSFODNN7EXAMPLE"\n'
    assert "AKIAIOSFODNN7EXAMPLE" not in redact(source)


def test_redact_masks_long_key_like_lines_but_keeps_the_indent() -> None:
    source = "    ghp_abcdef0123456789abcdef0123456789abcdefghij\n"
    redacted = redact(source)
    assert redacted == "    [REDACTED]\n"


def test_redact_leaves_ordinary_code_alone() -> None:
    source = "def handler(value):\n    return value * 2\n"
    assert redact(source) == source


# --- pick_similar -----------------------------------------------------------


def test_pick_similar_returns_the_closest_first() -> None:
    close = scored(chunk_id="c1", digest="d1", distance=0.1)
    far = scored(chunk_id="c2", digest="d2", distance=0.9)
    picked = pick_similar([far, close], frozenset(), LIMITS)
    assert [p.content_sha256 for p in picked] == ["d1", "d2"]


def test_pick_similar_respects_top_k() -> None:
    candidates = [scored(chunk_id=f"c{i}", digest=f"d{i}", distance=i / 10) for i in range(5)]
    picked = pick_similar(candidates, frozenset(), LIMITS)
    assert len(picked) == LIMITS.retrieval_top_k


def test_pick_similar_excludes_current_window_digests() -> None:
    candidates = [scored(chunk_id="c1", digest="d1", distance=0.1)]
    picked = pick_similar(candidates, frozenset({"d1"}), LIMITS)
    assert picked == []


def test_pick_similar_does_not_repeat_a_digest() -> None:
    candidates = [
        scored(chunk_id="c1", digest="d1", distance=0.1),
        scored(chunk_id="c2", digest="d1", distance=0.2),
    ]
    picked = pick_similar(candidates, frozenset(), LIMITS)
    assert len(picked) == 1


def test_pick_similar_skips_chunks_that_burst_the_byte_budget() -> None:
    small = scored(chunk_id="c1", digest="d1", distance=0.1, body="x = 1\n")
    huge = scored(
        chunk_id="c2", digest="d2", distance=0.2, body="y" * LIMITS.retrieval_byte_budget
    )
    picked = pick_similar([small, huge], frozenset(), LIMITS)
    assert [p.content_sha256 for p in picked] == ["d1"]


def test_chunk_draft_never_carries_a_secret() -> None:
    """Чанк уходит дальше по конвейеру уже чистым: redact применяется при сборке."""
    source = "TOKEN = AKIAIOSFODNN7EXAMPLE\nx = 1\n"
    limits = ProfileLimits(max_chunk_bytes=4096, retrieval_top_k=2, retrieval_byte_budget=100)
    draft = build_chunks([window(text=source)], REPO_ID, RUN_ID, limits)[0]
    assert "AKIAIOSFODNN7EXAMPLE" not in draft.body
    assert "[REDACTED]" in draft.body
    assert draft.body == "TOKEN = [REDACTED]\nx = 1\n"


def test_similar_chunk_shape() -> None:
    chunk = SimilarChunk(
        file_path="lib/old.py",
        start_line=1,
        end_line=5,
        content_sha256="d1",
        body="x = 1\n",
    )
    assert chunk.file_path == "lib/old.py"
