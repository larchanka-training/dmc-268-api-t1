"""Что слою приложения позволено знать о хранении.

Структурные протоколы: адаптер никогда не импортирует этот модуль, и у слоя
инфраструктуры нет compile-time зависимости от слоя приложения. Каждая
сигнатура говорит в доменных типах: ниже нет ни сессии, ни engine, ни
SQLAlchemy.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.entities import (
    ContextPayload,
    Finding,
    Hunk,
    MergeRequest,
    PublishedComment,
    Repository,
    ReviewRun,
)
from app.domain.enums import Provider
from app.domain.profile import EmbeddedChunk, ScoredChunk


class RepositoryRepo(Protocol):
    def get(self, repository_id: UUID) -> Repository | None: ...

    def find_by_provider(self, provider: Provider, provider_id: str) -> Repository | None: ...

    def add(self, repository: Repository) -> None: ...

    def update(self, repository: Repository) -> None: ...


class MergeRequestRepo(Protocol):
    def get(self, merge_request_id: UUID) -> MergeRequest | None: ...

    def find_by_number(self, repository_id: UUID, number: int) -> MergeRequest | None: ...

    def add(self, merge_request: MergeRequest) -> None: ...

    def update(self, merge_request: MergeRequest) -> None: ...


class ReviewRunRepo(Protocol):
    def get(self, run_id: UUID) -> ReviewRun | None: ...

    def find_active(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None: ...

    def list_unfinished(self) -> list[ReviewRun]: ...

    def add(self, run: ReviewRun) -> None: ...

    def update(self, run: ReviewRun) -> None: ...


class ContextPayloadRepo(Protocol):
    def list_for_run(self, review_run_id: UUID) -> list[ContextPayload]: ...

    def find_by_digest(self, content_sha256: str) -> ContextPayload | None: ...

    def add(self, payload: ContextPayload) -> None: ...


class FindingRepo(Protocol):
    def list_for_run(self, review_run_id: UUID) -> list[Finding]: ...

    def add(self, finding: Finding) -> None: ...

    def add_validated(
        self, finding: Finding, hunks: Iterable[Hunk], now: datetime
    ) -> None:
        """Сохранить замечание, только если его привязка внутри диффа.

        Объявлено здесь, потому что это единственный путь, который применяет
        правило, а проверка, доступная только через конкретный адаптер, — не
        проверка. `add` остаётся для вызывающих, которые уже проверили или
        которым не с какими hunk'ами сверяться.
        """
        ...


class PublishedCommentRepo(Protocol):
    def list_for_run(self, review_run_id: UUID) -> list[PublishedComment]: ...

    def add(self, comment: PublishedComment) -> None: ...


class CodeProfileRepo(Protocol):
    def add_many(self, chunks: Iterable[EmbeddedChunk]) -> None:
        """Дописать чанки в профиль; уже известные дайджесты пропускает."""
        ...

    def search(
        self,
        repository_id: UUID,
        embedding: Sequence[float],
        embedding_model: str,
        limit: int,
        exclude_digests: frozenset[str],
    ) -> list[ScoredChunk]:
        """Ближайшие чанки одного репозитория и одной модели вложений."""
        ...
