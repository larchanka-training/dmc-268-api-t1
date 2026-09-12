"""The constrained sets the schema and the pipeline both rely on.

Defined once here; the SQLAlchemy models build their native PostgreSQL enum
types from these, so a value cannot be added in one place and missed in the
other.
"""

from enum import StrEnum


class Provider(StrEnum):
    """Version-control host a repository lives on."""

    GITHUB = "github"
    GITLAB = "gitlab"


class TriggerSource(StrEnum):
    """What caused a review run to be created."""

    WEBHOOK = "webhook"
    MANUAL = "manual"
    MENTION = "mention"


class ReviewRunStatus(StrEnum):
    """Lifecycle of one attempt to review a commit."""

    QUEUED = "queued"
    BUILDING_CONTEXT = "building_context"
    ANALYSING = "analysing"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class FindingCategory(StrEnum):
    """The review dimensions a finding can belong to."""

    SECURITY = "security"
    CORRECTNESS = "correctness"
    PERFORMANCE = "performance"
    READABILITY = "readability"


class FindingSeverity(StrEnum):
    """How much a finding matters."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class CommentKind(StrEnum):
    """Shape of a comment published back to the host."""

    SUMMARY = "summary"
    INLINE = "inline"


class DiffSide(StrEnum):
    """Which side of a diff a line coordinate refers to."""

    OLD = "old"
    NEW = "new"


TERMINAL_STATUSES: frozenset[ReviewRunStatus] = frozenset(
    {
        ReviewRunStatus.COMPLETED,
        ReviewRunStatus.FAILED,
        ReviewRunStatus.CANCELLED,
    }
)
