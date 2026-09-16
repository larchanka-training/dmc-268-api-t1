"""What the application layer is allowed to know about persistence.

Structural protocols, so an adapter never imports this module and the
infrastructure layer stays free of any compile-time dependency on the
application layer. Every signature speaks in domain types: no session, no
engine, no SQLAlchemy anywhere below.
"""

from collections.abc import Iterable
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
        """Store a finding only if its anchor is inside the diff.

        Declared here because it is the only path that enforces the rule, and a
        guard reachable only through the concrete adapter is not a guard. `add`
        stays for callers that have already validated, or that have no hunks to
        validate against.
        """
        ...


class PublishedCommentRepo(Protocol):
    def list_for_run(self, review_run_id: UUID) -> list[PublishedComment]: ...

    def add(self, comment: PublishedComment) -> None: ...
