"""The records that outlive a request.

Frozen dataclasses with no framework in sight: constructible from literals,
comparable by value, and testable without a database. Times and identifiers
arrive as arguments rather than being read from a clock or a generator, so a
test can assert an exact timestamp instead of a range.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from app.domain.enums import (
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)


@dataclass(frozen=True, slots=True)
class Repository:
    """A repository the service is allowed to review."""

    id: UUID
    provider: Provider
    provider_id: str
    full_name: str
    default_branch: str
    auto_review_enabled: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class MergeRequest:
    """A change request on a host. Covers a GitHub pull request too."""

    id: UUID
    repository_id: UUID
    number: int
    title: str
    description: str
    author: str
    source_branch: str
    target_branch: str
    head_sha: str
    state: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewRun:
    """One attempt to review a change request at a specific commit.

    Persisted as `review_runs`. The ticket calls this a ReviewJob; that name is
    reserved for the queue message, so the durable record and the transient
    message never share a word.
    """

    id: UUID
    merge_request_id: UUID
    head_sha: str
    status: ReviewRunStatus
    trigger: TriggerSource
    last_progress_at: datetime
    created_at: datetime
    updated_at: datetime
    failure_reason: str | None = None
    model: str | None = None
    tokens_used: int | None = None
    duration_seconds: float | None = None
    rejected_findings: int = 0


@dataclass(frozen=True, slots=True)
class ContextPayload:
    """One chunk of the material sent to the model for a run."""

    id: UUID
    review_run_id: UUID
    chunk_index: int
    tiers: tuple[str, ...]
    file_paths: tuple[str, ...]
    token_count: int
    content_sha256: str
    body: dict
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class DiffAnchor:
    """Where in a diff a finding points.

    Construct through `validate_anchor`, which is what proves the coordinates
    are inside the change rather than an arbitrary line number.
    """

    file_path: str
    side: DiffSide
    old_line: int | None = None
    new_line: int | None = None


@dataclass(frozen=True, slots=True)
class Finding:
    """Something the review noticed, tied to a line the diff touched."""

    id: UUID
    review_run_id: UUID
    anchor: DiffAnchor
    category: FindingCategory
    severity: FindingSeverity
    message: str
    created_at: datetime
    updated_at: datetime
    suggestion: str | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class PublishedComment:
    """A comment the service posted back to the host."""

    id: UUID
    review_run_id: UUID
    provider_comment_id: str
    kind: CommentKind
    published_at: datetime
    created_at: datetime
    updated_at: datetime
    finding_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class Hunk:
    """A contiguous range a diff touched, as the parser reports it."""

    file_path: str
    old_start: int = 0
    old_count: int = 0
    new_start: int = 0
    new_count: int = 0
    changed_new_lines: frozenset[int] = field(default_factory=frozenset)
    changed_old_lines: frozenset[int] = field(default_factory=frozenset)
