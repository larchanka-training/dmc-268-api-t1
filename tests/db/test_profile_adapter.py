"""Адаптер профиля на живой PostgreSQL/pgvector.

То, что не видно на фейках: идемпотентность вставки, фильтры поиска,
косинусная близость, редакция тела в шве и — главное — поведение транзакции
при сбое: профиль best-effort и не имеет права калечить транзакцию прогона.
"""

from dataclasses import replace
from uuid import UUID

import pytest

from app.application.profile import IngestProfile, RetrieveSimilarCode
from app.domain.ids import new_id
from app.domain.profile import (
    EMBEDDING_DIMENSION,
    ChunkDraft,
    EmbeddedChunk,
    ProfileLimits,
    SurroundingWindow,
)
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from ..conftest import requires_db
from .test_adapters import a_merge_request, a_repository, a_run

pytestmark = [pytest.mark.integration, requires_db]

MODEL = "nomic-embed-text"
OTHER_MODEL = "other-embed-model"
LIMITS = ProfileLimits(
    max_chunk_bytes=4096, retrieval_top_k=5, retrieval_byte_budget=100000
)


@pytest.fixture
def uow(clean_db):
    return SqlAlchemyUnitOfWork(clean_db)


@pytest.fixture
def seeded(uow):
    """Репозиторий, MR и прогон — FK-основание для чанков профиля."""
    repo = a_repository()
    mr = a_merge_request(repo.id)
    run = a_run(mr.id)
    with uow as work:
        work.repositories.add(repo)
        work.merge_requests.add(mr)
        work.review_runs.add(run)
        work.commit()
    return repo, mr, run


def a_chunk(
    repo_id: UUID,
    run_id: UUID,
    *,
    digest: str = "d1",
    body: str = "x = 1\n",
    model: str = MODEL,
    axis: int = 0,
) -> EmbeddedChunk:
    """Чанк с вектором вдоль одной оси: `axis` задаёт «о чём» этот код."""
    embedding = [0.0] * EMBEDDING_DIMENSION
    embedding[axis] = 1.0
    return EmbeddedChunk(
        draft=ChunkDraft(
            review_run_id=run_id,
            repository_id=repo_id,
            file_path="app/main.py",
            start_line=1,
            end_line=10,
            commit_sha="abc123",
            content_sha256=digest,
            body=body,
        ),
        embedding=tuple(embedding),
        embedding_model=model,
    )


def a_query(axis: int = 0) -> list[float]:
    vector = [0.0] * EMBEDDING_DIMENSION
    vector[axis] = 1.0
    return vector


def a_window() -> SurroundingWindow:
    return SurroundingWindow(
        file_path="app/main.py",
        start_line=1,
        end_line=10,
        commit_sha="abc123",
        text="def handler():\n    return 1\n",
    )


class BadDimensionEmbedder:
    """Возвращает векторы чужой размерности — база обязана отказаться."""

    def embed(self, texts):
        return [[0.0, 0.0] for _ in texts]


def search_all(work, repo_id: UUID, *, model: str = MODEL):
    return work.code_profile.search(
        repo_id, a_query(), model, limit=10, exclude_digests=frozenset()
    )


def test_add_many_is_idempotent_for_the_same_digest(uow, seeded) -> None:
    repo, _, run = seeded
    with uow as work:
        work.code_profile.add_many([a_chunk(repo.id, run.id)])
        work.commit()
    with uow as work:
        # повторный прогон того же коммита производит те же дайджесты
        work.code_profile.add_many([a_chunk(repo.id, run.id)])
        work.commit()
    with uow as work:
        assert len(search_all(work, repo.id)) == 1


def test_search_stays_inside_the_repository(uow, seeded) -> None:
    repo, _, run = seeded
    other_repo = replace(a_repository(), id=new_id(), provider_id="2")
    other_mr = replace(a_merge_request(other_repo.id), id=new_id(), repository_id=other_repo.id, number=2)
    other_run = replace(a_run(other_mr.id), id=new_id(), merge_request_id=other_mr.id)
    with uow as work:
        work.repositories.add(other_repo)
        work.merge_requests.add(other_mr)
        work.review_runs.add(other_run)
        work.code_profile.add_many(
            [a_chunk(repo.id, run.id, digest="mine"),
             a_chunk(other_repo.id, other_run.id, digest="theirs")]
        )
        work.commit()
    with uow as work:
        found = search_all(work, repo.id)
    assert [c.content_sha256 for c in found] == ["mine"]


def test_search_ignores_chunks_of_other_models(uow, seeded) -> None:
    repo, _, run = seeded
    with uow as work:
        work.code_profile.add_many(
            [a_chunk(repo.id, run.id, digest="d-model", model=MODEL),
             a_chunk(repo.id, run.id, digest="d-other", model=OTHER_MODEL)]
        )
        work.commit()
    with uow as work:
        found = search_all(work, repo.id, model=MODEL)
    assert [c.content_sha256 for c in found] == ["d-model"]


def test_search_excludes_the_given_digests(uow, seeded) -> None:
    repo, _, run = seeded
    with uow as work:
        work.code_profile.add_many(
            [a_chunk(repo.id, run.id, digest="d1"), a_chunk(repo.id, run.id, digest="d2")]
        )
        work.commit()
    with uow as work:
        found = work.code_profile.search(
            repo.id, a_query(), MODEL, limit=10, exclude_digests=frozenset({"d1"})
        )
    assert [c.content_sha256 for c in found] == ["d2"]


def test_search_orders_by_cosine_distance(uow, seeded) -> None:
    repo, _, run = seeded
    with uow as work:
        work.code_profile.add_many(
            [
                a_chunk(repo.id, run.id, digest="far", axis=1),
                a_chunk(repo.id, run.id, digest="close", axis=0),
            ]
        )
        work.commit()
    with uow as work:
        found = search_all(work, repo.id)
    assert [c.content_sha256 for c in found] == ["close", "far"]
    assert found[0].distance < found[1].distance


def test_add_many_stores_the_body_redacted(uow, seeded) -> None:
    repo, _, run = seeded
    secret = 'AWS_SECRET_ACCESS_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"'
    with uow as work:
        work.code_profile.add_many([a_chunk(repo.id, run.id, body=secret + "\n")])
        work.commit()
    with uow as work:
        found = search_all(work, repo.id)
    assert "wJalrXUtnFEMI" not in found[0].body
    assert "AWS_SECRET_ACCESS_KEY = [REDACTED]" in found[0].body


def test_a_search_failure_leaves_the_runs_transaction_usable(uow, seeded) -> None:
    """Best-effort на деле: сбой поиска не переводит сессию в rollback-required."""
    repo, mr, _ = seeded
    with uow as work:
        retrieve = RetrieveSimilarCode(
            BadDimensionEmbedder(), work.code_profile, MODEL, LIMITS
        )
        assert retrieve.retrieve([a_window()], repo.id) == []
        another = replace(a_run(mr.id), id=new_id(), head_sha="def456")
        work.review_runs.add(another)
        work.commit()
    with uow as work:
        assert work.review_runs.get(another.id) is not None


def test_an_ingest_failure_leaves_the_runs_transaction_usable(uow, seeded) -> None:
    repo, mr, run = seeded
    with uow as work:
        ingest = IngestProfile(BadDimensionEmbedder(), work.code_profile, MODEL, LIMITS)
        ingest.ingest([a_window()], repo.id, run.id)
        another = replace(a_run(mr.id), id=new_id(), head_sha="def456")
        work.review_runs.add(another)
        work.commit()
    with uow as work:
        assert work.review_runs.get(another.id) is not None
