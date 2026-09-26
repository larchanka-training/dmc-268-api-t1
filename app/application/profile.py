"""Use case'ы профиля репозитория: retrieval похожего кода и накопление.

Оба шага best-effort по спеке repo-rag-profile: сбой эмбеддера или хранилища
гасится здесь, на границе use case'а, и ни при каких условиях не меняет исход
прогона ревью — уровень `similar` просто не собирается. Вызывает их воркер
пайплайна ревью: retrieval до сборки промпта, накопление после.
"""

import logging
from uuid import UUID

from app.application.ports.embedding_gateway import EmbeddingGateway
from app.application.ports.repositories import CodeProfileRepo
from app.domain.profile import (
    EmbeddedChunk,
    ProfileLimits,
    SimilarChunk,
    SurroundingWindow,
    build_chunks,
    content_digest,
    pick_similar,
    redact,
)

logger = logging.getLogger(__name__)


class RetrieveSimilarCode:
    """Собрать уровень `similar` из профиля репозитория."""

    def __init__(
        self,
        embedder: EmbeddingGateway,
        profiles: CodeProfileRepo,
        embedding_model: str,
        limits: ProfileLimits,
    ) -> None:
        self._embedder = embedder
        self._profiles = profiles
        self._model = embedding_model
        self._limits = limits

    def retrieve(
        self, windows: list[SurroundingWindow], repository_id: UUID
    ) -> list[SimilarChunk]:
        """Похожие чанки под лимитами; на любой сбой — пустой уровень."""
        try:
            return self._retrieve(windows, repository_id)
        except Exception:  # noqa: BLE001 — граница best-effort: любой сбой профиля не роняет прогон
            logger.warning("profile retrieval failed; continuing without `similar`")
            return []

    def _retrieve(
        self, windows: list[SurroundingWindow], repository_id: UUID
    ) -> list[SimilarChunk]:
        if not windows:
            return []
        # Запрос и хранение вложат один и тот же отредактированный текст:
        # вектор секрета — тоже утечка, хоть и косвенная.
        queries = [redact(w.text) for w in windows]
        # Самосовпадения исключаются дайджестом исходного окна — ровно тем,
        # под которым окно лежит в профиле.
        current_digests = frozenset(content_digest(w.text) for w in windows)
        vectors = self._embedder.embed(queries)
        scored = []
        for vector in vectors:
            scored.extend(
                self._profiles.search(
                    repository_id,
                    vector,
                    self._model,
                    self._limits.retrieval_top_k,
                    current_digests,
                )
            )
        return pick_similar(scored, current_digests, self._limits)


class IngestProfile:
    """Пополнить профиль репозитория окнами окружения прогона."""

    def __init__(
        self,
        embedder: EmbeddingGateway,
        profiles: CodeProfileRepo,
        embedding_model: str,
        limits: ProfileLimits,
    ) -> None:
        self._embedder = embedder
        self._profiles = profiles
        self._model = embedding_model
        self._limits = limits

    def ingest(
        self, windows: list[SurroundingWindow], repository_id: UUID, review_run_id: UUID
    ) -> None:
        """Чанки прогона — в профиль; на любой сбой — просто без пополнения."""
        try:
            self._ingest(windows, repository_id, review_run_id)
        except Exception:  # noqa: BLE001 — граница best-effort: любой сбой профиля не роняет прогон
            logger.warning("profile ingestion failed; run continues without it")

    def _ingest(
        self, windows: list[SurroundingWindow], repository_id: UUID, review_run_id: UUID
    ) -> None:
        drafts = build_chunks(windows, repository_id, review_run_id, self._limits)
        if not drafts:
            return
        vectors = self._embedder.embed([draft.body for draft in drafts])
        self._profiles.add_many(
            EmbeddedChunk(draft=draft, embedding=tuple(vector), embedding_model=self._model)
            for draft, vector in zip(drafts, vectors, strict=True)
        )
