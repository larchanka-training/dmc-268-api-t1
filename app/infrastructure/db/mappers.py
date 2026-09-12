"""Translation between rows and domain entities.

Nothing here branches on business rules. If a rule appears to live in a
mapper, it belongs in a pure function the caller consults first.
"""

from copy import deepcopy

from app.domain.entities import (
    ContextPayload,
    DiffAnchor,
    Finding,
    MergeRequest,
    PublishedComment,
    Repository,
    ReviewRun,
)
from app.infrastructure.db.models import (
    ContextPayloadRow,
    FindingRow,
    MergeRequestRow,
    PublishedCommentRow,
    RepositoryRow,
    ReviewRunRow,
)


def repository_to_domain(row: RepositoryRow) -> Repository:
    return Repository(
        id=row.id,
        provider=row.provider,
        provider_id=row.provider_id,
        full_name=row.full_name,
        default_branch=row.default_branch,
        auto_review_enabled=row.auto_review_enabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def merge_request_to_domain(row: MergeRequestRow) -> MergeRequest:
    return MergeRequest(
        id=row.id,
        repository_id=row.repository_id,
        number=row.number,
        title=row.title,
        description=row.description,
        author=row.author,
        source_branch=row.source_branch,
        target_branch=row.target_branch,
        head_sha=row.head_sha,
        state=row.state,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def review_run_to_domain(row: ReviewRunRow) -> ReviewRun:
    return ReviewRun(
        id=row.id,
        merge_request_id=row.merge_request_id,
        head_sha=row.head_sha,
        status=row.status,
        trigger=row.trigger,
        last_progress_at=row.last_progress_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        failure_reason=row.failure_reason,
        model=row.model,
        tokens_used=row.tokens_used,
        duration_seconds=row.duration_seconds,
        rejected_findings=row.rejected_findings,
    )


def context_payload_to_domain(row: ContextPayloadRow) -> ContextPayload:
    return ContextPayload(
        id=row.id,
        review_run_id=row.review_run_id,
        chunk_index=row.chunk_index,
        tiers=tuple(row.tiers),
        file_paths=tuple(row.file_paths),
        token_count=row.token_count,
        content_sha256=row.content_sha256,
        # Copied, like tiers and file_paths above. Handing the row's dict out
        # by reference would make a frozen dataclass a live handle on the
        # session's state, where mutating payload.body flushes to the database.
        body=deepcopy(row.body),
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def finding_to_domain(row: FindingRow) -> Finding:
    return Finding(
        id=row.id,
        review_run_id=row.review_run_id,
        anchor=DiffAnchor(
            file_path=row.file_path,
            side=row.side,
            old_line=row.old_line,
            new_line=row.new_line,
        ),
        category=row.category,
        severity=row.severity,
        message=row.message,
        created_at=row.created_at,
        updated_at=row.updated_at,
        suggestion=row.suggestion,
        confidence=row.confidence,
    )


def published_comment_to_domain(row: PublishedCommentRow) -> PublishedComment:
    return PublishedComment(
        id=row.id,
        review_run_id=row.review_run_id,
        provider_comment_id=row.provider_comment_id,
        kind=row.kind,
        published_at=row.published_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        finding_id=row.finding_id,
    )
