"""The transaction boundary.

A use case takes a unit of work, reaches the repositories through it, and
commits once. Nothing below this line knows what a session is.
"""

from types import TracebackType
from typing import Protocol, Self

from app.application.ports.repositories import (
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

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
