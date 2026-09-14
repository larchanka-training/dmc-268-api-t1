"""SQLAlchemy implementations of the persistence ports.

Thin by design: each method translates between a row and a domain entity and
does nothing else. Two of them consult a pure function before writing, and
those are the only places any rule appears. They are marked.
"""

import datetime as dt
from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.diff import validate_anchor
from app.domain.entities import (
    ContextPayload,
    Finding,
    Hunk,
    MergeRequest,
    PublishedComment,
    Repository,
    ReviewRun,
)
from app.domain.enums import TERMINAL_STATUSES, Provider
from app.domain.lifecycle import next_status
from app.infrastructure.db import mappers as m
from app.infrastructure.db.models import (
    ContextPayloadRow,
    FindingRow,
    MergeRequestRow,
    PublishedCommentRow,
    RepositoryRow,
    ReviewRunRow,
)


class SqlAlchemyRepositoryRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, repository_id: UUID) -> Repository | None:
        row = self._session.get(RepositoryRow, repository_id)
        return m.repository_to_domain(row) if row else None

    def find_by_provider(self, provider: Provider, provider_id: str) -> Repository | None:
        row = self._session.scalars(
            select(RepositoryRow).where(
                RepositoryRow.provider == provider,
                RepositoryRow.provider_id == provider_id,
            )
        ).one_or_none()
        return m.repository_to_domain(row) if row else None

    def add(self, repository: Repository) -> None:
        self._session.add(
            RepositoryRow(
                id=repository.id,
                provider=repository.provider,
                provider_id=repository.provider_id,
                full_name=repository.full_name,
                default_branch=repository.default_branch,
                auto_review_enabled=repository.auto_review_enabled,
            )
        )

    def update(self, repository: Repository) -> None:
        row = self._session.get(RepositoryRow, repository.id)
        if row is None:
            raise LookupError(f"repository {repository.id} is not stored")
        row.full_name = repository.full_name
        row.default_branch = repository.default_branch
        row.auto_review_enabled = repository.auto_review_enabled


class SqlAlchemyMergeRequestRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, merge_request_id: UUID) -> MergeRequest | None:
        row = self._session.get(MergeRequestRow, merge_request_id)
        return m.merge_request_to_domain(row) if row else None

    def find_by_number(self, repository_id: UUID, number: int) -> MergeRequest | None:
        row = self._session.scalars(
            select(MergeRequestRow).where(
                MergeRequestRow.repository_id == repository_id,
                MergeRequestRow.number == number,
            )
        ).one_or_none()
        return m.merge_request_to_domain(row) if row else None

    def add(self, merge_request: MergeRequest) -> None:
        self._session.add(
            MergeRequestRow(
                id=merge_request.id,
                repository_id=merge_request.repository_id,
                number=merge_request.number,
                title=merge_request.title,
                description=merge_request.description,
                author=merge_request.author,
                source_branch=merge_request.source_branch,
                target_branch=merge_request.target_branch,
                head_sha=merge_request.head_sha,
                state=merge_request.state,
            )
        )

    def update(self, merge_request: MergeRequest) -> None:
        row = self._session.get(MergeRequestRow, merge_request.id)
        if row is None:
            raise LookupError(f"merge request {merge_request.id} is not stored")
        row.title = merge_request.title
        row.description = merge_request.description
        row.source_branch = merge_request.source_branch
        row.target_branch = merge_request.target_branch
        row.head_sha = merge_request.head_sha
        row.state = merge_request.state


class SqlAlchemyReviewRunRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, run_id: UUID) -> ReviewRun | None:
        row = self._session.get(ReviewRunRow, run_id)
        return m.review_run_to_domain(row) if row else None

    def find_active(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        row = self._session.scalars(
            select(ReviewRunRow).where(
                ReviewRunRow.merge_request_id == merge_request_id,
                ReviewRunRow.head_sha == head_sha,
                ReviewRunRow.status.not_in(TERMINAL_STATUSES),
            )
        ).one_or_none()
        return m.review_run_to_domain(row) if row else None

    def list_unfinished(self) -> list[ReviewRun]:
        rows = self._session.scalars(
            select(ReviewRunRow).where(ReviewRunRow.status.not_in(TERMINAL_STATUSES))
        ).all()
        return [m.review_run_to_domain(row) for row in rows]

    def add(self, run: ReviewRun) -> None:
        """Insert a run, carrying every field it already holds.

        At insert the entity is the only source, including for a run rebuilt
        from an earlier attempt, so the outcome fields and the rejection count
        are mapped here even though `update` leaves the count alone.
        """
        self._session.add(
            ReviewRunRow(
                id=run.id,
                merge_request_id=run.merge_request_id,
                head_sha=run.head_sha,
                status=run.status,
                trigger=run.trigger,
                last_progress_at=run.last_progress_at,
                failure_reason=run.failure_reason,
                model=run.model,
                tokens_used=run.tokens_used,
                duration_seconds=run.duration_seconds,
                rejected_findings=run.rejected_findings,
            )
        )

    def update(self, run: ReviewRun) -> None:
        """Persist a run's progress, refusing a status the state machine disallows.

        The only rule in this class, and it is delegated: `next_status` owns
        the transition table, so the adapter cannot drift from the domain.

        `rejected_findings` is deliberately absent. `add_validated` increments
        it on the row without the caller's entity ever hearing about it, so
        writing the entity's copy back here would undo the rejection it just
        recorded. The row owns that counter; everything else is the caller's.
        """
        row = self._session.get(ReviewRunRow, run.id)
        if row is None:
            raise LookupError(f"review run {run.id} is not stored")
        if row.status != run.status:
            verdict = next_status(row.status, run.status)
            if not verdict.ok:
                raise ValueError(verdict.error)
        row.status = run.status
        row.last_progress_at = run.last_progress_at
        row.failure_reason = run.failure_reason
        row.model = run.model
        row.tokens_used = run.tokens_used
        row.duration_seconds = run.duration_seconds


class SqlAlchemyContextPayloadRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_run(self, review_run_id: UUID) -> list[ContextPayload]:
        rows = self._session.scalars(
            select(ContextPayloadRow)
            .where(ContextPayloadRow.review_run_id == review_run_id)
            .order_by(ContextPayloadRow.chunk_index)
        ).all()
        return [m.context_payload_to_domain(row) for row in rows]

    def find_by_digest(self, content_sha256: str) -> ContextPayload | None:
        """The oldest payload with this digest.

        The digest index is not unique, and reuse is the whole point of the
        lookup, so several rows can match. Ordering by `id` makes the answer
        the same on every call: the ids are time-ordered UUIDv7.
        """
        row = self._session.scalars(
            select(ContextPayloadRow)
            .where(ContextPayloadRow.content_sha256 == content_sha256)
            .order_by(ContextPayloadRow.id)
        ).first()
        return m.context_payload_to_domain(row) if row else None

    def add(self, payload: ContextPayload) -> None:
        self._session.add(
            ContextPayloadRow(
                id=payload.id,
                review_run_id=payload.review_run_id,
                chunk_index=payload.chunk_index,
                tiers=list(payload.tiers),
                file_paths=list(payload.file_paths),
                token_count=payload.token_count,
                content_sha256=payload.content_sha256,
                body=payload.body,
            )
        )


class SqlAlchemyFindingRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_run(self, review_run_id: UUID) -> list[Finding]:
        rows = self._session.scalars(
            select(FindingRow).where(FindingRow.review_run_id == review_run_id)
        ).all()
        return [m.finding_to_domain(row) for row in rows]

    def add(self, finding: Finding) -> None:
        self._session.add(
            FindingRow(
                id=finding.id,
                review_run_id=finding.review_run_id,
                file_path=finding.anchor.file_path,
                side=finding.anchor.side,
                old_line=finding.anchor.old_line,
                new_line=finding.anchor.new_line,
                category=finding.category,
                severity=finding.severity,
                message=finding.message,
                suggestion=finding.suggestion,
                confidence=finding.confidence,
            )
        )

    def add_validated(
        self, finding: Finding, hunks: Iterable[Hunk], now: dt.datetime
    ) -> None:
        """Store a finding only if its anchor is inside the diff.

        The rule itself is `validate_anchor`; this counts the rejection on the
        run so a filtered finding leaves a trace instead of vanishing.
        """
        verdict = validate_anchor(finding.anchor, hunks)
        if not verdict.ok:
            row = self._session.get(ReviewRunRow, finding.review_run_id)
            if row is not None:
                row.rejected_findings += 1
                row.last_progress_at = now
            raise ValueError(verdict.error)
        self.add(finding)


class SqlAlchemyPublishedCommentRepo:
    def __init__(self, session: Session) -> None:
        self._session = session

    def list_for_run(self, review_run_id: UUID) -> list[PublishedComment]:
        rows = self._session.scalars(
            select(PublishedCommentRow).where(
                PublishedCommentRow.review_run_id == review_run_id
            )
        ).all()
        return [m.published_comment_to_domain(row) for row in rows]

    def add(self, comment: PublishedComment) -> None:
        self._session.add(
            PublishedCommentRow(
                id=comment.id,
                review_run_id=comment.review_run_id,
                finding_id=comment.finding_id,
                provider_comment_id=comment.provider_comment_id,
                kind=comment.kind,
                published_at=comment.published_at,
            )
        )
