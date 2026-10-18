"""Что слою приложения позволено знать о хранении.

Структурные протоколы: адаптер никогда не импортирует этот модуль, и у слоя
инфраструктуры нет compile-time зависимости от слоя приложения. Каждая
сигнатура говорит в доменных типах: ниже нет ни сессии, ни engine, ни
SQLAlchemy.
"""

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.domain.entities import (
    ContextPayload,
    Finding,
    MergeRequest,
    OpenReviewThread,
    PublishedComment,
    Repository,
    ReviewRun,
    Session,
    User,
)
from app.domain.enums import Provider


class RepositoryRepo(Protocol):
    def get(self, repository_id: UUID) -> Repository | None: ...

    def find_by_provider(self, provider: Provider, provider_id: str) -> Repository | None: ...

    def list_all(self, limit: int, offset: int) -> list[Repository]: ...

    def add(self, repository: Repository) -> None: ...

    def update(self, repository: Repository) -> None: ...


class MergeRequestRepo(Protocol):
    def get(self, merge_request_id: UUID) -> MergeRequest | None: ...

    def find_by_number(self, repository_id: UUID, number: int) -> MergeRequest | None: ...

    def list_for_repository(
        self, repository_id: UUID, limit: int, offset: int
    ) -> list[MergeRequest]: ...

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


class PublishedCommentRepo(Protocol):
    def list_for_run(self, review_run_id: UUID) -> list[PublishedComment]: ...

    def list_open_threads_for_merge_request(
        self, merge_request_id: UUID
    ) -> list[OpenReviewThread]: ...

    def get(self, published_comment_id: UUID) -> PublishedComment | None: ...

    def find_by_provider_comment_id(self, provider_comment_id: str) -> PublishedComment | None: ...

    def add(self, comment: PublishedComment) -> None: ...

    def update(self, comment: PublishedComment) -> None: ...

    def record_feedback(self, provider_comment_id: str, *, positive: bool) -> bool: ...


class UserRepo(Protocol):
    def get(self, user_id: UUID) -> User | None: ...

    def find_by_provider(self, provider: Provider, provider_user_id: str) -> User | None: ...

    def add(self, user: User) -> bool:
        """Вставить пользователя; `False` — пара провайдер + аккаунт уже занята.

        Два параллельных первых входа одного аккаунта не должны давать ошибку
        базы: проигравший получает `False` и обновляет найденную строку.
        """
        ...

    def update(self, user: User) -> None: ...


class SessionRepo(Protocol):
    def find_by_token_hash(self, token_hash: bytes) -> Session | None: ...

    def add(self, session: Session) -> None: ...

    def delete(self, session_id: UUID) -> None:
        """Удалить сессию; отсутствующая — не ошибка: выход идемпотентен."""
        ...

    def delete_expired(self, now: datetime) -> int:
        """Удалить сессии, срок которых истёк к `now`; вернуть их число."""
        ...
