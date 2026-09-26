"""Граница транзакции.

Use case принимает unit of work, через него добирается до репозиториев и
делает один коммит. Ниже этой строки никто не знает, что такое сессия.
"""

from types import TracebackType
from typing import Protocol, Self

from app.application.ports.repositories import (
    CodeProfileRepo,
    ContextPayloadRepo,
    FindingRepo,
    MergeRequestRepo,
    PublishedCommentRepo,
    RepositoryRepo,
    ReviewRunRepo,
)


class UnitOfWork(Protocol):
    repositories: RepositoryRepo
    merge_requests: MergeRequestRepo
    review_runs: ReviewRunRepo
    context_payloads: ContextPayloadRepo
    findings: FindingRepo
    published_comments: PublishedCommentRepo
    code_profile: CodeProfileRepo

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
