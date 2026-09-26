"""Use case'ы профиля на фейках портов: ни базы, ни сети.

Поведение из спеки repo-rag-profile: retrieval до накопления, лимиты,
самосовпадения, best-effort — сбой порта не роняет прогон.
"""

import datetime as dt
from uuid import UUID

from app.application.profile import IngestProfile, RetrieveSimilarCode
from app.domain.profile import (
    EmbeddedChunk,
    ProfileLimits,
    ScoredChunk,
    SimilarChunk,
    SurroundingWindow,
)

NOW = dt.datetime(2026, 9, 26, 12, 0, tzinfo=dt.UTC)
REPO_ID = UUID(int=10**24)
RUN_ID = UUID(int=10**25)
MODEL = "nomic-embed-text"


class FakeEmbedder:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[list[str]] = []

    def embed(self, texts):
        if self.fail:
            raise ConnectionError("ollama is down")
        self.calls.append(list(texts))
        return [[float(len(t)), 0.0] for t in texts]


class FakeProfileRepo:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.stored: list[EmbeddedChunk] = []
        self.search_results: list[ScoredChunk] = []

    def add_many(self, chunks) -> None:
        if self.fail:
            raise RuntimeError("write failed")
        self.stored.extend(chunks)

    def search(self, repository_id, embedding, embedding_model, limit, exclude_digests):
        return [
            c for c in self.search_results if c.content_sha256 not in exclude_digests
        ][:limit]


def window(
    *, text: str = "def handler():\n    return 1\n", path: str = "app/main.py"
) -> SurroundingWindow:
    return SurroundingWindow(
        file_path=path,
        start_line=1,
        end_line=10,
        commit_sha="sha1",
        text=text,
    )


def limits() -> ProfileLimits:
    return ProfileLimits(
        max_chunk_bytes=4096, retrieval_top_k=3, retrieval_byte_budget=100000
    )


def test_ingest_stores_embedded_chunks() -> None:
    embedder = FakeEmbedder()
    repo = FakeProfileRepo()
    ingest = IngestProfile(embedder, repo, MODEL, limits())
    ingest.ingest([window()], REPO_ID, RUN_ID)
    assert len(repo.stored) == 1
    stored = repo.stored[0]
    assert stored.draft.repository_id == REPO_ID
    assert stored.draft.review_run_id == RUN_ID
    assert stored.embedding_model == MODEL
    assert stored.embedding == (len(window().text), 0.0)


def test_ingest_survives_a_repo_failure() -> None:
    ingest = IngestProfile(FakeEmbedder(), FakeProfileRepo(fail=True), MODEL, limits())
    ingest.ingest([window()], REPO_ID, RUN_ID)  # не бросает


def test_ingest_survives_an_embedder_failure() -> None:
    ingest = IngestProfile(
        FakeEmbedder(fail=True), FakeProfileRepo(), MODEL, limits()
    )
    ingest.ingest([window()], REPO_ID, RUN_ID)  # не бросает


def test_ingest_of_no_windows_calls_nothing() -> None:
    embedder = FakeEmbedder()
    repo = FakeProfileRepo()
    ingest = IngestProfile(embedder, repo, MODEL, limits())
    ingest.ingest([], REPO_ID, RUN_ID)
    assert embedder.calls == []
    assert repo.stored == []


def test_retrieve_returns_similar_chunks() -> None:
    embedder = FakeEmbedder()
    repo = FakeProfileRepo()
    repo.search_results = [
        ScoredChunk(
            chunk_id="c1",
            file_path="lib/old.py",
            start_line=1,
            end_line=5,
            content_sha256="d1",
            body="x = 1\n",
            distance=0.1,
        )
    ]
    retrieve = RetrieveSimilarCode(embedder, repo, MODEL, limits())
    assert retrieve.retrieve([window()], REPO_ID) == [
        SimilarChunk(
            file_path="lib/old.py",
            start_line=1,
            end_line=5,
            content_sha256="d1",
            body="x = 1\n",
        )
    ]


def test_retrieve_excludes_the_current_windows_own_digest() -> None:
    """Самосовпадение: то же окно уже в профиле — в similar ему нечего делать."""
    embedder = FakeEmbedder()
    repo = FakeProfileRepo()
    w = window()
    retrieve = RetrieveSimilarCode(embedder, repo, MODEL, limits())
    assert retrieve.retrieve([w], REPO_ID) == []


def test_retrieve_survives_an_embedder_failure() -> None:
    retrieve = RetrieveSimilarCode(
        FakeEmbedder(fail=True), FakeProfileRepo(), MODEL, limits()
    )
    assert retrieve.retrieve([window()], REPO_ID) == []


def test_retrieve_survives_a_repo_failure() -> None:
    retrieve = RetrieveSimilarCode(
        FakeEmbedder(), FakeProfileRepo(fail=True), MODEL, limits()
    )
    assert retrieve.retrieve([window()], REPO_ID) == []


def test_retrieve_batches_texts_into_one_embed_call() -> None:
    """Один вызов эмбеддера на все окна, порядок сохраняется."""
    embedder = FakeEmbedder()
    repo = FakeProfileRepo()
    retrieve = RetrieveSimilarCode(embedder, repo, MODEL, limits())
    w1, w2 = window(), window(path="lib/other.py", text="y = 2\n")
    retrieve.retrieve([w1, w2], REPO_ID)
    assert embedder.calls == [[w1.text, w2.text]]
