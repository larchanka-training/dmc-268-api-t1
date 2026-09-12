"""The review pipeline's tables.

Enum columns are built from the domain enums, so a value cannot be added in one
place and forgotten in the other. Native PostgreSQL enum types rather than text
plus a CHECK, because the constrained sets are part of the contract and should
hold against every writer, including a psql session.
"""

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.enums import (
    TERMINAL_STATUSES,
    CommentKind,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    Provider,
    ReviewRunStatus,
    TriggerSource,
)
from app.infrastructure.db.base import Base, TimestampMixin, UuidPrimaryKeyMixin

_TERMINAL_SQL = ", ".join(f"'{s.value}'" for s in sorted(TERMINAL_STATUSES))


def _enum(python_enum: type, name: str) -> SAEnum:
    """Native PostgreSQL enum built from the single domain definition."""
    return SAEnum(
        python_enum,
        name=name,
        native_enum=True,
        values_callable=lambda e: [member.value for member in e],
    )


class RepositoryRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "repositories"
    __table_args__ = (
        UniqueConstraint("provider", "provider_id", name="uq_repositories_provider_id"),
    )

    provider: Mapped[Provider] = mapped_column(_enum(Provider, "provider"), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(512), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    auto_review_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    merge_requests: Mapped[list[MergeRequestRow]] = relationship(
        back_populates="repository", cascade="all, delete-orphan", passive_deletes=True
    )


class MergeRequestRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """A change request. Covers a GitHub pull request as well as a GitLab MR."""

    __tablename__ = "merge_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_merge_requests_repo_number"),
    )

    repository_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("repositories.id", ondelete="CASCADE"), nullable=False, index=True
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(String(255), nullable=False)
    source_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    target_branch: Mapped[str] = mapped_column(String(255), nullable=False)
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)

    repository: Mapped[RepositoryRow] = relationship(back_populates="merge_requests")
    review_runs: Mapped[list[ReviewRunRow]] = relationship(
        back_populates="merge_request", cascade="all, delete-orphan", passive_deletes=True
    )


class ReviewRunRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """One attempt to review a change request at a specific commit.

    The ticket calls this a ReviewJob. That name is reserved for the queue
    message, so the durable row and the transient message never share a word.
    """

    __tablename__ = "review_runs"
    __table_args__ = (
        # At most one unfinished run per commit. This is what stops a redelivered
        # webhook starting a second review, and it is why an abandoned run has to
        # be reaped: it would otherwise block its commit forever.
        Index(
            "uq_review_runs_one_active_per_commit",
            "merge_request_id",
            "head_sha",
            unique=True,
            postgresql_where=text(f"status NOT IN ({_TERMINAL_SQL})"),
        ),
    )

    merge_request_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("merge_requests.id", ondelete="CASCADE"), nullable=False, index=True
    )
    head_sha: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[ReviewRunStatus] = mapped_column(
        _enum(ReviewRunStatus, "review_run_status"), nullable=False
    )
    trigger: Mapped[TriggerSource] = mapped_column(
        _enum(TriggerSource, "trigger_source"), nullable=False
    )
    last_progress_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    failure_reason: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(255))
    tokens_used: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    rejected_findings: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    merge_request: Mapped[MergeRequestRow] = relationship(back_populates="review_runs")


class ContextPayloadRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """What the model was shown, kept so a run stays reproducible.

    This table holds other people's source code verbatim. Redaction of secrets
    belongs before the insert, in the context builder; this is the sensitive
    table the retention policy is really about.
    """

    __tablename__ = "context_payloads"
    __table_args__ = (
        UniqueConstraint("review_run_id", "chunk_index", name="uq_context_payloads_chunk"),
        Index("ix_context_payloads_digest", "content_sha256"),
    )

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tiers: Mapped[list[str]] = mapped_column(ARRAY(String(32)), nullable=False)
    file_paths: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False)


class FindingRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """Something the review noticed, anchored to a line the diff touched."""

    __tablename__ = "findings"
    __table_args__ = (
        # Collapsing duplicates is the database's job, so no insertion path can
        # forget it. The domain also deduplicates, which lets the caller learn
        # what was dropped instead of catching an integrity error.
        #
        # The key is the whole anchor. Only the line number belonging to the
        # anchor's side is populated, so NULLS NOT DISTINCT is what makes the
        # constraint fire at all on the old side, where every new_line is NULL.
        UniqueConstraint(
            "review_run_id",
            "file_path",
            "side",
            "old_line",
            "new_line",
            "category",
            name="uq_findings_anchor",
            postgresql_nulls_not_distinct=True,
        ),
    )

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    side: Mapped[DiffSide] = mapped_column(_enum(DiffSide, "diff_side"), nullable=False)
    old_line: Mapped[int | None] = mapped_column(Integer)
    new_line: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[FindingCategory] = mapped_column(
        _enum(FindingCategory, "finding_category"), nullable=False
    )
    severity: Mapped[FindingSeverity] = mapped_column(
        _enum(FindingSeverity, "finding_severity"), nullable=False
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    suggestion: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float | None] = mapped_column(Float)


class PublishedCommentRow(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """A comment posted back to the host."""

    __tablename__ = "published_comments"
    __table_args__ = (
        # A finding is published at most once per run, so a retry cannot
        # double-post. A summary carries no finding, and NULLS NOT DISTINCT is
        # what extends the same limit to it: without it Postgres treats every
        # NULL finding_id as unique and a retried publish stores a second
        # summary for the run.
        UniqueConstraint(
            "review_run_id",
            "finding_id",
            name="uq_published_comments_finding",
            postgresql_nulls_not_distinct=True,
        ),
    )

    review_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("review_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    finding_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("findings.id", ondelete="CASCADE")
    )
    provider_comment_id: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[CommentKind] = mapped_column(_enum(CommentKind, "comment_kind"), nullable=False)
    published_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
