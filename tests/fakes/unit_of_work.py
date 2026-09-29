"""Фейковый `UnitOfWork` — для тестов прикладного слоя без БД.

Подменяет порт, не вендора: каждый фейк реализует тот же протокол, что и
`SqlAlchemy*`-адаптер, включая единственные правила, которые адаптеры
проверяют сами (`next_status` в `review_runs.update`, `validate_anchor` в
`findings.add_validated`), чтобы тест на фейке и тест на реальной базе видели
одно и то же поведение.
"""

from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime
from types import TracebackType
from typing import Self
from uuid import UUID

from app.application.ports.repositories import (
    ContextPayloadRepo,
    FindingRepo,
    MergeRequestRepo,
    PublishedCommentRepo,
    RepositoryRepo,
    ReviewRunRepo,
)
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


class FakeRepositoryRepo:
    def __init__(self) -> None:
        self._by_id: dict[UUID, Repository] = {}

    def get(self, repository_id: UUID) -> Repository | None:
        return self._by_id.get(repository_id)

    def find_by_provider(self, provider: Provider, provider_id: str) -> Repository | None:
        return next(
            (
                r
                for r in self._by_id.values()
                if r.provider == provider and r.provider_id == provider_id
            ),
            None,
        )

    def list_all(self, limit: int, offset: int) -> list[Repository]:
        ordered = sorted(self._by_id.values(), key=lambda r: r.created_at)
        return ordered[offset : offset + limit]

    def add(self, repository: Repository) -> None:
        self._by_id[repository.id] = repository

    def update(self, repository: Repository) -> None:
        if repository.id not in self._by_id:
            raise LookupError(f"repository {repository.id} is not stored")
        self._by_id[repository.id] = repository


class FakeMergeRequestRepo:
    def __init__(self) -> None:
        self._by_id: dict[UUID, MergeRequest] = {}

    def get(self, merge_request_id: UUID) -> MergeRequest | None:
        return self._by_id.get(merge_request_id)

    def find_by_number(self, repository_id: UUID, number: int) -> MergeRequest | None:
        return next(
            (
                m
                for m in self._by_id.values()
                if m.repository_id == repository_id and m.number == number
            ),
            None,
        )

    def list_for_repository(
        self, repository_id: UUID, limit: int, offset: int
    ) -> list[MergeRequest]:
        ordered = sorted(
            (m for m in self._by_id.values() if m.repository_id == repository_id),
            key=lambda m: m.number,
        )
        return ordered[offset : offset + limit]

    def add(self, merge_request: MergeRequest) -> None:
        self._by_id[merge_request.id] = merge_request

    def update(self, merge_request: MergeRequest) -> None:
        if merge_request.id not in self._by_id:
            raise LookupError(f"merge request {merge_request.id} is not stored")
        self._by_id[merge_request.id] = merge_request


class FakeReviewRunRepo:
    def __init__(self) -> None:
        self._by_id: dict[UUID, ReviewRun] = {}

    def get(self, run_id: UUID) -> ReviewRun | None:
        return self._by_id.get(run_id)

    def find_active(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        return next(
            (
                r
                for r in self._by_id.values()
                if r.merge_request_id == merge_request_id
                and r.head_sha == head_sha
                and r.status not in TERMINAL_STATUSES
            ),
            None,
        )

    def list_unfinished(self) -> list[ReviewRun]:
        return [r for r in self._by_id.values() if r.status not in TERMINAL_STATUSES]

    def add(self, run: ReviewRun) -> None:
        self._by_id[run.id] = run

    def update(self, run: ReviewRun) -> None:
        existing = self._by_id.get(run.id)
        if existing is None:
            raise LookupError(f"review run {run.id} is not stored")
        verdict = next_status(existing.status, run.status)
        if not verdict.ok:
            raise ValueError(verdict.error)
        self._by_id[run.id] = run

    def bump_rejected(self, run_id: UUID, now: datetime) -> None:
        """Не часть порта: используется только `FakeFindingRepo.add_validated`."""
        run = self._by_id.get(run_id)
        if run is not None:
            self._by_id[run_id] = replace(
                run, rejected_findings=run.rejected_findings + 1, last_progress_at=now
            )


class FakeContextPayloadRepo:
    def __init__(self) -> None:
        self._items: list[ContextPayload] = []

    def list_for_run(self, review_run_id: UUID) -> list[ContextPayload]:
        return [p for p in self._items if p.review_run_id == review_run_id]

    def find_by_digest(self, content_sha256: str) -> ContextPayload | None:
        return next((p for p in self._items if p.content_sha256 == content_sha256), None)

    def add(self, payload: ContextPayload) -> None:
        self._items.append(payload)


class FakeFindingRepo:
    def __init__(self, review_runs: FakeReviewRunRepo) -> None:
        self._items: list[Finding] = []
        self._review_runs = review_runs

    def list_for_run(self, review_run_id: UUID) -> list[Finding]:
        return [f for f in self._items if f.review_run_id == review_run_id]

    def add(self, finding: Finding) -> None:
        self._items.append(finding)

    def add_validated(self, finding: Finding, hunks: Iterable[Hunk], now: datetime) -> None:
        verdict = validate_anchor(finding.anchor, hunks)
        if not verdict.ok:
            self._review_runs.bump_rejected(finding.review_run_id, now)
            raise ValueError(verdict.error)
        self.add(finding)


class FakePublishedCommentRepo:
    def __init__(self) -> None:
        self._items: list[PublishedComment] = []

    def list_for_run(self, review_run_id: UUID) -> list[PublishedComment]:
        return [c for c in self._items if c.review_run_id == review_run_id]

    def add(self, comment: PublishedComment) -> None:
        self._items.append(comment)


class FakeUnitOfWork:
    """Держит состояние между повторными входами в `with` — как настоящая база."""

    def __init__(self) -> None:
        # Типы атрибутов — порты, не конкретные фейки: то же самое, что
        # `SqlAlchemyUnitOfWork` делает для структурного совпадения с
        # протоколом `UnitOfWork` (см. её `__enter__`). Локальная переменная
        # до аннотации нужна `FakeFindingRepo`, которому — единственному —
        # нужен доступ к `bump_rejected`, отсутствующему в самом протоколе.
        review_runs = FakeReviewRunRepo()
        self.repositories: RepositoryRepo = FakeRepositoryRepo()
        self.merge_requests: MergeRequestRepo = FakeMergeRequestRepo()
        self.review_runs: ReviewRunRepo = review_runs
        self.context_payloads: ContextPayloadRepo = FakeContextPayloadRepo()
        self.findings: FindingRepo = FakeFindingRepo(review_runs)
        self.published_comments: PublishedCommentRepo = FakePublishedCommentRepo()
        self.committed = 0
        self.rolled_back = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.rollback()

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1
