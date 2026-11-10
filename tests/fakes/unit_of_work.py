"""Фейковый `UnitOfWork` — для тестов прикладного слоя без БД.

Подменяет порт, не вендора: каждый фейк реализует тот же протокол, что и
`SqlAlchemy*`-адаптер, включая единственное правило, которое адаптер
проверяет сам (`next_status` в `review_runs.update`), чтобы тест на фейке и
тест на реальной базе видели одно и то же поведение.
"""

from collections import Counter
from collections.abc import Collection
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from app.application.ports.unit_of_work import RunStateConflict, UnitOfWork
from app.domain.analytics import (
    EMPTY_ANALYTICS,
    Analytics,
    AnalyticsFilter,
    FindingsDayCount,
    RepositoryAnalytics,
    RunDurations,
)
from app.domain.billing import eur_per_token
from app.domain.entities import (
    BalanceCredit,
    BotThread,
    ContextPayload,
    DiffAnchor,
    Finding,
    MergeRequest,
    OpenReviewThread,
    ProviderToken,
    PublishedComment,
    Repository,
    RepositorySettings,
    ReviewRun,
    ReviewRunListEntry,
    ReviewRunStep,
    Session,
    StoredContextPayload,
    User,
)
from app.domain.enums import (
    REVIEWED_STATUSES,
    TERMINAL_STATUSES,
    CommentKind,
    CoverageReason,
    DiffSide,
    FindingCategory,
    FindingSeverity,
    Provider,
    ReviewRunStatus,
    ReviewRunStepStatus,
    TriggerSource,
)
from app.domain.lifecycle import next_status
from app.domain.llm_usage import LlmUsage
from app.domain.payload_retention import BodyExpiryStats
from app.domain.run_feed import FeedCursor
from app.domain.run_list import RunListFilter, RunListPage


class FakeRepositoryRepo:
    def __init__(self) -> None:
        self._by_id: dict[UUID, Repository] = {}

    @property
    def stored(self) -> dict[tuple[Provider, str], Repository]:
        """Вид по естественному ключу — для тестов приёма вебхуков."""
        return {(r.provider, r.provider_id): r for r in self._by_id.values()}

    def snapshot(self) -> dict[UUID, Repository]:
        return dict(self._by_id)

    def restore(self, state: dict[UUID, Repository]) -> None:
        self._by_id = dict(state)

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

    def list_by_provider_ids(self, provider: Provider, provider_ids: Collection[str]) -> list[Repository]:
        wanted = set(provider_ids)
        return sorted(
            (r for r in self._by_id.values() if r.provider == provider and r.provider_id in wanted),
            key=lambda r: r.id,
        )

    def add(self, repository: Repository) -> bool:
        if self.find_by_provider(repository.provider, repository.provider_id) is not None:
            return False
        self._by_id[repository.id] = repository
        return True

    def reconnect(self, repository: Repository) -> Repository | None:
        stored = self.find_by_provider(repository.provider, repository.provider_id)
        if stored is None or stored.connected_at is not None:
            return None
        reconnected = replace(
            stored,
            connected_at=repository.connected_at,
            full_name=repository.full_name,
            default_branch=repository.default_branch,
            updated_at=repository.updated_at,
            payer_user_id=stored.payer_user_id or repository.payer_user_id,
        )
        self._by_id[stored.id] = reconnected
        return reconnected

    def disconnect(self, repository_id: UUID, now: datetime) -> None:
        stored = self._by_id.get(repository_id)
        if stored is not None:
            self._by_id[repository_id] = replace(stored, connected_at=None, updated_at=now)

    def update_settings(self, repository_id: UUID, settings: RepositorySettings, now: datetime) -> Repository | None:
        stored = self._by_id.get(repository_id)
        if stored is None:
            return None
        saved = replace(
            stored,
            auto_review_enabled=settings.auto_review_enabled,
            branch_filter=settings.branch_filter,
            min_severity=settings.min_severity,
            updated_at=now,
        )
        self._by_id[repository_id] = saved
        return saved

    def set_payer(self, repository_id: UUID, user_id: UUID, now: datetime) -> Repository | None:
        stored = self._by_id.get(repository_id)
        if stored is None:
            return None
        self._by_id[repository_id] = replace(stored, payer_user_id=user_id, updated_at=now)
        return self._by_id[repository_id]

    def drop_payer(self, repository_id: UUID, user_id: UUID, now: datetime) -> bool:
        stored = self._by_id.get(repository_id)
        if stored is None or stored.payer_user_id != user_id:
            return False
        self._by_id[repository_id] = replace(stored, payer_user_id=None, updated_at=now)
        return True

    def list_paid_by(self, user_id: UUID) -> list[Repository]:
        paid = [r for r in self._by_id.values() if r.payer_user_id == user_id]
        return sorted(paid, key=lambda r: r.full_name)


class FakeMergeRequestRepo:
    def __init__(self) -> None:
        self._by_id: dict[UUID, MergeRequest] = {}
        # Что добавлено/обновлено через этот фейк — тестам приёма, которые
        # сверяют исход с точными записями.
        self.added: list[MergeRequest] = []
        self.updated: list[MergeRequest] = []
        self._added_mark = 0

    @property
    def by_number(self) -> dict[tuple[UUID, int], MergeRequest]:
        """Вид по естественному ключу — для тестов приёма вебхуков."""
        return {(m.repository_id, m.number): m for m in self._by_id.values()}

    def snapshot(self) -> dict[UUID, MergeRequest]:
        # Отметка added — rollback позже уберёт добавленное после снимка,
        # как это сделал бы настоящий UoW.
        self._added_mark = len(self.added)
        return dict(self._by_id)

    def restore(self, state: dict[UUID, MergeRequest]) -> None:
        self._by_id = dict(state)
        del self.added[self._added_mark :]

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
        self.added.append(merge_request)
        self._by_id[merge_request.id] = merge_request

    def update(self, merge_request: MergeRequest) -> None:
        if merge_request.id not in self._by_id:
            raise LookupError(f"merge request {merge_request.id} is not stored")
        self.updated.append(merge_request)
        self._by_id[merge_request.id] = merge_request


def _average(durations: list[float]) -> float:
    return sum(durations) / len(durations)


def _p95(durations: list[float]) -> float:
    """`percentile_cont(0.95)` Postgres: линейная интерполяция между соседними значениями."""
    values = sorted(durations)
    position = 0.95 * (len(values) - 1)
    low = int(position)
    high = min(low + 1, len(values) - 1)
    return values[low] + (values[high] - values[low]) * (position - low)


def _matches(entry: ReviewRunListEntry, run_filter: RunListFilter) -> bool:
    run, merge_request = entry.run, entry.merge_request
    if merge_request.repository_id not in run_filter.repository_ids:
        return False
    if run_filter.statuses is not None and run.status not in run_filter.statuses:
        return False
    if run_filter.author is not None and merge_request.author != run_filter.author:
        return False
    if run_filter.text is not None:
        needle = run_filter.text.lower()
        haystack = (merge_request.title, merge_request.author, merge_request.source_branch)
        if not any(needle in value.lower() for value in haystack) and merge_request.number != run_filter.number:
            return False
    if run_filter.created_from is not None and run.created_at < run_filter.created_from:
        return False
    return run_filter.created_to is None or run.created_at < run_filter.created_to


class FakeReviewRunRepo:
    def __init__(self) -> None:
        self._by_id: dict[UUID, ReviewRun] = {}
        # Что добавлено через этот фейк — тестам приёма вебхуков.
        self.added: list[ReviewRun] = []
        self._added_mark = 0

    @property
    def runs(self) -> list[ReviewRun]:
        return list(self._by_id.values())

    def snapshot(self) -> dict[UUID, ReviewRun]:
        # Отметка added — rollback позже уберёт добавленное после снимка,
        # как это сделал бы настоящий UoW.
        self._added_mark = len(self.added)
        return dict(self._by_id)

    def restore(self, state: dict[UUID, ReviewRun]) -> None:
        self._by_id = dict(state)
        del self.added[self._added_mark :]

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

    def find_reviewed(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        reviewed = [
            r
            for r in self._by_id.values()
            if r.merge_request_id == merge_request_id
            and r.head_sha == head_sha
            and r.trigger is TriggerSource.WEBHOOK
            and r.status in REVIEWED_STATUSES
        ]
        return max(reviewed, key=lambda r: (r.created_at, r.id), default=None)

    def find_completed_manual(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        completed = [
            r
            for r in self._by_id.values()
            if r.merge_request_id == merge_request_id
            and r.head_sha == head_sha
            and r.trigger is TriggerSource.MANUAL
            and r.status is ReviewRunStatus.COMPLETED
            and r.coverage_reason is None
        ]
        return max(completed, key=lambda r: (r.created_at, r.id), default=None)

    def find_latest(self, merge_request_id: UUID, head_sha: str) -> ReviewRun | None:
        runs = [
            r
            for r in self._by_id.values()
            if r.merge_request_id == merge_request_id and r.head_sha == head_sha
        ]
        return max(runs, key=lambda r: (r.created_at, r.id), default=None)

    def list_unfinished(self) -> list[ReviewRun]:
        return [r for r in self._by_id.values() if r.status not in TERMINAL_STATUSES]

    def bind_published(self, published_comments: FakePublishedCommentRepo) -> None:
        self._published = published_comments

    def list_unpublished_failures(self, merge_request_id: UUID) -> list[ReviewRun]:
        runs = [r for r in self._by_id.values() if r.merge_request_id == merge_request_id]
        last_published_at = max(
            (c.published_at for r in runs for c in self._published.list_for_run(r.id)),
            default=None,
        )
        return sorted(
            (
                r
                for r in runs
                if r.status is ReviewRunStatus.FAILED
                and r.failed_stage is ReviewRunStatus.PUBLISHING
                and not self._published.list_for_run(r.id)
                and (last_published_at is None or r.last_progress_at > last_published_at)
            ),
            key=lambda r: (r.created_at, r.id),
        )

    def bind(
        self,
        merge_requests: FakeMergeRequestRepo,
        repositories: FakeRepositoryRepo,
        findings: FakeFindingRepo,
    ) -> None:
        """Связать с соседними фейками: `list_changed` собирает строку списка, как JOIN."""
        self._merge_requests = merge_requests
        self._repositories = repositories
        self._findings = findings

    def list_changed(
        self,
        after: FeedCursor,
        recheck_from: datetime,
        repository_ids: Collection[UUID],
        limit: int,
    ) -> list[ReviewRunListEntry]:
        # Как SQL-адаптер: limit только у части новее курсора, окно — целиком.
        entries: list[ReviewRunListEntry] = []
        for run in self._by_id.values():
            merge_request = self._merge_requests.get(run.merge_request_id)
            if merge_request is None or merge_request.repository_id not in repository_ids:
                continue
            repository = self._repositories.get(merge_request.repository_id)
            assert repository is not None
            count = len(self._findings.list_for_run(run.id))
            entries.append(ReviewRunListEntry(run, merge_request, repository, count))
        entries.sort(key=lambda e: (e.run.updated_at, e.run.id))
        fresh = [e for e in entries if FeedCursor(e.run.updated_at, e.run.id) > after][:limit]
        late = [
            e
            for e in entries
            if e.run.updated_at > recheck_from and FeedCursor(e.run.updated_at, e.run.id) <= after
        ]
        return sorted(fresh + late, key=lambda e: (e.run.updated_at, e.run.id))

    def list_page(self, run_filter: RunListFilter, limit: int, offset: int) -> RunListPage:
        # Как SQL-адаптер: ILIKE — подстрока через lower(), не casefold(); номер — точное совпадение.
        entries = [entry for entry in self._entries() if _matches(entry, run_filter)]
        entries.sort(key=lambda e: (e.run.created_at, e.run.id), reverse=True)
        return RunListPage(entries=entries[offset : offset + limit], total=len(entries))

    def analytics(self, analytics_filter: AnalyticsFilter) -> Analytics:
        # Как SQL-адаптер: completed не без денег, только новые замечания (повтор треда не считается),
        # день — UTC-дата created_at прогона, p95 — percentile_cont (линейная интерполяция).
        if not analytics_filter.repository_ids:
            return EMPTY_ANALYTICS
        f = analytics_filter
        counted = [
            e
            for e in self._entries()
            if e.merge_request.repository_id in f.repository_ids
            and e.run.status is ReviewRunStatus.COMPLETED
            and e.run.coverage_reason != CoverageReason.BALANCE
            and f.created_from <= e.run.created_at < f.created_to
        ]
        new_findings = {
            e.run.id: [x for x in self._findings.list_for_run(e.run.id) if x.covered_by_finding_id is None] for e in counted
        }
        by_day: Counter[tuple[date, FindingCategory, FindingSeverity]] = Counter()
        for e in counted:
            for finding in new_findings[e.run.id]:
                by_day[(e.run.created_at.astimezone(UTC).date(), finding.category, finding.severity)] += 1
        order = {value: index for enum in (FindingCategory, FindingSeverity) for index, value in enumerate(enum)}
        days = sorted(by_day.items(), key=lambda item: (item[0][0], order[item[0][1]], order[item[0][2]]))
        # Как CHECK в базе: у завершённого прогона длительность есть всегда.
        def duration(e: ReviewRunListEntry) -> float:
            assert e.run.duration_seconds is not None
            return e.run.duration_seconds

        durations = [duration(e) for e in counted]
        repositories: dict[UUID, list[ReviewRunListEntry]] = {}
        for e in counted:
            repositories.setdefault(e.repository.id, []).append(e)
        return Analytics(
            findings_by_day=tuple(FindingsDayCount(day, category, severity, count) for (day, category, severity), count in days),
            runs=RunDurations(len(counted), _average(durations) if durations else None, _p95(durations) if durations else None),
            by_repository=tuple(
                RepositoryAnalytics(
                    entries[0].repository.id, entries[0].repository.full_name, len(entries),
                    _average([duration(e) for e in entries]), _p95([duration(e) for e in entries]),
                    sum(len(new_findings[e.run.id]) for e in entries),
                )
                for entries in sorted(repositories.values(), key=lambda es: (es[0].repository.full_name, es[0].repository.id))
            ),
        )

    def _entries(self) -> list[ReviewRunListEntry]:
        entries: list[ReviewRunListEntry] = []
        for run in self._by_id.values():
            merge_request = self._merge_requests.get(run.merge_request_id)
            assert merge_request is not None
            repository = self._repositories.get(merge_request.repository_id)
            assert repository is not None
            entries.append(ReviewRunListEntry(run, merge_request, repository, len(self._findings.list_for_run(run.id))))
        return entries

    def add(self, run: ReviewRun) -> None:
        self.added.append(run)
        self._by_id[run.id] = run

    def update(self, run: ReviewRun, *, previous: ReviewRun | None = None) -> None:
        existing = self._by_id.get(run.id)
        if existing is None:
            raise LookupError(f"review run {run.id} is not stored")
        if previous is not None and (existing.status, existing.last_progress_at) != (
            previous.status,
            previous.last_progress_at,
        ):
            raise RunStateConflict(f"review run {run.id} changed since it was read")
        # Как у SQL-адаптера: проверка перехода — только при смене статуса или
        # для терминальной строки.
        if existing.status in TERMINAL_STATUSES or existing.status != run.status:
            verdict = next_status(existing.status, run.status)
            if not verdict.ok:
                raise ValueError(verdict.error)
        # Как у SQL-адаптера: поля биллинга пишутся один раз, устаревшая копия их не стирает.
        self._by_id[run.id] = replace(
            run,
            payer_user_id=run.payer_user_id or existing.payer_user_id,
            estimated_cost_eur=existing.estimated_cost_eur
            if run.estimated_cost_eur is None
            else run.estimated_cost_eur,
            coverage_reason=run.coverage_reason or existing.coverage_reason,
        )

    def record_cancelled_spend(self, run_id: UUID, usage: LlmUsage) -> None:
        existing = self._by_id.get(run_id)
        if existing is not None and existing.status is ReviewRunStatus.CANCELLED:
            self._by_id[run_id] = replace(existing, tokens_used=usage.total_tokens, usage=usage)


class FakeReviewRunStepRepo:
    def __init__(self) -> None:
        self._by_id: dict[UUID, ReviewRunStep] = {}

    @property
    def steps(self) -> list[ReviewRunStep]:
        """Все шаги в порядке начала — как их отдаёт SQL-адаптер."""
        return sorted(self._by_id.values(), key=lambda s: (s.started_at, s.id))

    def snapshot(self) -> dict[UUID, ReviewRunStep]:
        return dict(self._by_id)

    def restore(self, state: dict[UUID, ReviewRunStep]) -> None:
        self._by_id = dict(state)

    def list_for_run(self, review_run_id: UUID) -> list[ReviewRunStep]:
        return [s for s in self.steps if s.review_run_id == review_run_id]

    def add(self, step: ReviewRunStep) -> None:
        self._by_id[step.id] = step

    def update(self, step: ReviewRunStep) -> None:
        existing = self._by_id.get(step.id)
        if existing is None:
            raise LookupError(f"review run step {step.id} is not stored")
        if existing.status is ReviewRunStepStatus.RUNNING:
            self._by_id[step.id] = step


class FakeContextPayloadRepo:
    def __init__(self) -> None:
        self._items: list[StoredContextPayload] = []

    def snapshot(self) -> list[StoredContextPayload]:
        return list(self._items)

    def restore(self, state: list[StoredContextPayload]) -> None:
        self._items = list(state)

    def list_for_run(self, review_run_id: UUID) -> list[StoredContextPayload]:
        return [p for p in self._items if p.review_run_id == review_run_id]

    def add(self, payload: ContextPayload) -> None:
        self._items.append(
            StoredContextPayload(
                id=payload.id,
                review_run_id=payload.review_run_id,
                chunk_index=payload.chunk_index,
                tiers=payload.tiers,
                file_paths=payload.file_paths,
                token_count=payload.token_count,
                content_sha256=payload.content_sha256,
                body=payload.body,
                body_expired_at=None,
                created_at=payload.created_at,
                updated_at=payload.updated_at,
            )
        )

    def expire_bodies(self, created_before: datetime, now: datetime, limit: int) -> int:
        live = sorted(
            (p for p in self._items if p.body_expired_at is None and p.created_at < created_before),
            key=lambda p: p.created_at,
        )[:limit]
        expired = {p.id for p in live}
        self._items = [
            replace(p, body=None, body_expired_at=now, updated_at=now) if p.id in expired else p
            for p in self._items
        ]
        return len(expired)

    def body_expiry_stats_for_run(self, review_run_id: UUID) -> BodyExpiryStats:
        expiries = [p.body_expired_at for p in self.list_for_run(review_run_id)]
        erased = [at for at in expiries if at is not None]
        return BodyExpiryStats(
            payload_count=len(expiries),
            erased_count=len(erased),
            last_expired_at=max(erased, default=None),
        )


class FakeFindingRepo:
    def __init__(self, review_runs: FakeReviewRunRepo) -> None:
        self._items: list[Finding] = []
        self._review_runs = review_runs

    def snapshot(self) -> list[Finding]:
        return list(self._items)

    def restore(self, state: list[Finding]) -> None:
        self._items = list(state)

    def list_for_run(self, review_run_id: UUID) -> list[Finding]:
        return sorted(
            (f for f in self._items if f.review_run_id == review_run_id),
            key=lambda f: (f.created_at, f.id),
        )

    def add(self, finding: Finding) -> None:
        self._items.append(finding)

    def mark_covered(self, finding_id: UUID, covered_by_finding_id: UUID) -> None:
        for index, item in enumerate(self._items):
            if item.id == finding_id:
                self._items[index] = replace(item, covered_by_finding_id=covered_by_finding_id)
                return
        raise LookupError(f"finding {finding_id} is not stored")


class FakePublishedCommentRepo:
    def __init__(self, review_runs: FakeReviewRunRepo) -> None:
        self._items: list[PublishedComment] = []
        self._review_runs = review_runs
        self._findings: FakeFindingRepo | None = None

    def bind_findings(self, findings: FakeFindingRepo) -> None:
        self._findings = findings

    def snapshot(self) -> list[PublishedComment]:
        return list(self._items)

    def restore(self, state: list[PublishedComment]) -> None:
        self._items = list(state)

    def list_for_run(self, review_run_id: UUID) -> list[PublishedComment]:
        return sorted(
            (c for c in self._items if c.review_run_id == review_run_id),
            key=lambda c: (c.published_at, c.id),
        )

    def list_open_threads_for_merge_request(
        self, merge_request_id: UUID
    ) -> list[OpenReviewThread]:
        return [
            OpenReviewThread(
                finding=t.finding,
                root_provider_comment_id=t.root_provider_comment_id,
                root_published_comment_id=t.root_published_comment_id,
            )
            for t in self.list_threads_for_merge_request(merge_request_id)
            if not t.resolved
        ]

    def list_threads_for_merge_request(self, merge_request_id: UUID) -> list[BotThread]:
        if self._findings is None:
            return []
        runs = {
            r.id: r
            for r in self._review_runs.runs
            if r.merge_request_id == merge_request_id
        }
        threads: list[BotThread] = []
        for comment in sorted(self._items, key=lambda c: (c.published_at, c.id)):
            if comment.kind is not CommentKind.INLINE:
                continue
            run = runs.get(comment.review_run_id)
            if run is None or comment.finding_id is None:
                continue
            finding = next(
                (f for f in self._findings._items if f.id == comment.finding_id),
                None,
            )
            if finding is None:
                continue
            threads.append(
                BotThread(
                    finding=finding,
                    root_provider_comment_id=comment.provider_comment_id,
                    root_published_comment_id=comment.id,
                    resolved=comment.thread_resolved,
                    resolved_by_bot=comment.thread_resolved_by_bot,
                )
            )
        return threads

    def get(self, published_comment_id: UUID) -> PublishedComment | None:
        return next((c for c in self._items if c.id == published_comment_id), None)

    def find_by_provider_comment_id(self, provider_comment_id: str) -> PublishedComment | None:
        return next(
            (c for c in self._items if c.provider_comment_id == provider_comment_id),
            None,
        )

    def add(self, comment: PublishedComment) -> None:
        self._items.append(comment)

    def update(self, comment: PublishedComment) -> None:
        for index, existing in enumerate(self._items):
            if existing.id == comment.id:
                self._items[index] = comment
                return
        raise LookupError(f"published comment {comment.id} is not stored")

    def record_feedback(self, provider_comment_id: str, *, positive: bool) -> bool:
        comment = self.find_by_provider_comment_id(provider_comment_id)
        if comment is None:
            return False
        if positive:
            updated = replace(comment, feedback_positive=comment.feedback_positive + 1)
        else:
            updated = replace(comment, feedback_negative=comment.feedback_negative + 1)
        self.update(updated)
        return True


class FakeUserRepo:
    def __init__(self) -> None:
        self._by_id: dict[UUID, User] = {}

    def snapshot(self) -> dict[UUID, User]:
        return dict(self._by_id)

    def restore(self, state: dict[UUID, User]) -> None:
        self._by_id = dict(state)

    def get(self, user_id: UUID) -> User | None:
        return self._by_id.get(user_id)

    def find_by_provider(self, provider: Provider, provider_user_id: str) -> User | None:
        return next(
            (
                u
                for u in self._by_id.values()
                if u.provider == provider and u.provider_user_id == provider_user_id
            ),
            None,
        )

    def add(self, user: User) -> bool:
        if self.find_by_provider(user.provider, user.provider_user_id) is not None:
            return False
        self._by_id[user.id] = user
        return True

    def update(self, user: User) -> None:
        if user.id not in self._by_id:
            raise LookupError(f"user {user.id} is not stored")
        # Как SQL-адаптер: меняются только профиль, ключ и created_at — нет.
        stored = self._by_id[user.id]
        self._by_id[user.id] = replace(
            stored, login=user.login, name=user.name, avatar_url=user.avatar_url
        )


class FakeSessionRepo:
    def __init__(self) -> None:
        self._by_id: dict[UUID, Session] = {}

    @property
    def stored(self) -> list[Session]:
        return list(self._by_id.values())

    def snapshot(self) -> dict[UUID, Session]:
        return dict(self._by_id)

    def restore(self, state: dict[UUID, Session]) -> None:
        self._by_id = dict(state)

    def find_by_token_hash(self, token_hash: bytes) -> Session | None:
        return next((s for s in self._by_id.values() if s.token_hash == token_hash), None)

    def add(self, session: Session) -> None:
        self._by_id[session.id] = session

    def delete(self, session_id: UUID) -> None:
        self._by_id.pop(session_id, None)

    def delete_expired(self, now: datetime) -> int:
        expired = [s.id for s in self._by_id.values() if now >= s.expires_at]
        for session_id in expired:
            del self._by_id[session_id]
        return len(expired)


class FakeProviderTokenRepo:
    """Токены по id сессии. Каскад базы — фильтром по живым сессиям: токен
    удалённой сессии не виден, как и в настоящей базе; токен без сессии не
    пишется (внешний ключ)."""

    def __init__(self, sessions: FakeSessionRepo) -> None:
        self._sessions = sessions
        self._by_session: dict[UUID, ProviderToken] = {}
        self.locked: list[UUID] = []

    @property
    def stored(self) -> dict[UUID, ProviderToken]:
        live = {s.id for s in self._sessions.stored}
        return {k: v for k, v in self._by_session.items() if k in live}

    def snapshot(self) -> dict[UUID, ProviderToken]:
        return dict(self._by_session)

    def restore(self, state: dict[UUID, ProviderToken]) -> None:
        self._by_session = dict(state)

    def get(self, session_id: UUID) -> ProviderToken | None:
        return self.stored.get(session_id)

    def exists(self, session_id: UUID) -> bool:
        return session_id in self.stored

    def get_for_update(self, session_id: UUID) -> ProviderToken | None:
        self.locked.append(session_id)
        return self.get(session_id)

    def save(self, session_id: UUID, token: ProviderToken) -> None:
        if session_id not in {s.id for s in self._sessions.stored}:
            raise LookupError(f"session {session_id} is not stored")
        self._by_session[session_id] = token


class FakeBillingRepo:
    """Баланс по зачислениям и прогонам фейка `review_runs` — та же формула, что у SQL (D1)."""

    def __init__(self, review_runs: FakeReviewRunRepo) -> None:
        self._review_runs = review_runs
        self.credits: list[BalanceCredit] = []
        # Кого блокировали, по порядку: тесту видно, что проверка шла под блокировкой.
        self.locked: list[UUID] = []

    def snapshot(self) -> list[BalanceCredit]:
        return list(self.credits)

    def restore(self, state: list[BalanceCredit]) -> None:
        self.credits = list(state)

    def lock_payer(self, user_id: UUID) -> None:
        self.locked.append(user_id)

    def balance(self, user_id: UUID) -> Decimal:
        credited = sum((c.amount_eur for c in self.credits if c.user_id == user_id), Decimal(0))
        spent = sum((_run_spend(r) for r in self._review_runs.runs if r.payer_user_id == user_id), Decimal(0))
        return credited - spent

    def eur_per_token(self) -> Decimal:
        paid = sorted(
            (r.created_at, r.id, r.usage)
            for r in self._review_runs.runs
            if r.usage is not None and r.usage.cost_eur > 0
        )[-50:]
        return eur_per_token(
            sum((usage.cost_eur for _, _, usage in paid), Decimal(0)),
            sum(usage.total_tokens for _, _, usage in paid),
        )

    def add_credit(self, credit: BalanceCredit) -> None:
        self.credits.append(credit)


def _run_spend(run: ReviewRun) -> Decimal:
    if run.usage is not None:
        return run.usage.cost_eur
    return run.estimated_cost_eur or Decimal(0)


class FakeUnitOfWork(UnitOfWork):
    """Держит состояние между повторными входами в `with` — как настоящая база.

    Наследует протокол номинально: изменяемые атрибуты mypy проверяет
    инвариантно, поэтому конкретные типы фейков совместимы с ним только
    через базу.

    Транзакционный: `rollback` возвращает состояние на момент последнего
    `commit` (или входа в `with`), поэтому тест на фейке видит то же, что и
    тест на настоящей базе, — в том числе потерю всего, что не закоммичено.
    Вне `with` (как в тестах роутера, где зависимость подменена) снимка нет, и
    `rollback` ничего не откатывает.
    """

    def __init__(self) -> None:
        # Типы атрибутов — конкретные фейки, не протоколы: тестам нужен и
        # порт, и фейковый API поверх него (`added`, `stored`, `by_number`).
        # Протокол `UnitOfWork` фейк наследует номинально — инвариантность
        # изменяемых атрибутов проверяется именно наследованием.
        self._fakes = _Fakes()
        self.repositories: FakeRepositoryRepo = self._fakes.repositories
        self.merge_requests: FakeMergeRequestRepo = self._fakes.merge_requests
        self.review_runs: FakeReviewRunRepo = self._fakes.review_runs
        self.review_run_steps: FakeReviewRunStepRepo = self._fakes.review_run_steps
        self.context_payloads: FakeContextPayloadRepo = self._fakes.context_payloads
        self.findings: FakeFindingRepo = self._fakes.findings
        self.published_comments: FakePublishedCommentRepo = self._fakes.published_comments
        self.published_comments.bind_findings(self.findings)
        self.review_runs.bind_published(self.published_comments)
        self.users: FakeUserRepo = self._fakes.users
        self.sessions: FakeSessionRepo = self._fakes.sessions
        self.provider_tokens: FakeProviderTokenRepo = self._fakes.provider_tokens
        self.billing: FakeBillingRepo = self._fakes.billing
        self.committed = 0
        self.rolled_back = 0
        self._committed_state: list[Any] | None = None
        # Отказы коммита — сцены гонки доставок: постоянный (`commit_error`)
        # и одноразовые (`commit_errors`, расходуются по одному на коммит).
        self.commit_error: Exception | None = None
        self.commit_errors: list[Exception] = []

    @property
    def commits(self) -> int:
        """Псевдоним `committed` — имя, которое ждут тесты приёма вебхуков."""
        return self.committed

    @property
    def rollbacks(self) -> int:
        return self.rolled_back

    def __enter__(self) -> Self:
        self._committed_state = self._fakes.snapshot()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.rollback()
        self._committed_state = None

    def commit(self) -> None:
        self.committed += 1
        if self.commit_errors:
            raise self.commit_errors.pop(0)
        if self.commit_error is not None:
            raise self.commit_error
        if self._committed_state is not None:
            self._committed_state = self._fakes.snapshot()

    def rollback(self) -> None:
        self.rolled_back += 1
        if self._committed_state is not None:
            self._fakes.restore(self._committed_state)


class _Fakes:
    """Все фейки-репозитории и их совместные снимки."""

    def __init__(self) -> None:
        self.repositories = FakeRepositoryRepo()
        self.merge_requests = FakeMergeRequestRepo()
        self.review_runs = FakeReviewRunRepo()
        self.review_run_steps = FakeReviewRunStepRepo()
        self.context_payloads = FakeContextPayloadRepo()
        self.findings = FakeFindingRepo(self.review_runs)
        self.review_runs.bind(self.merge_requests, self.repositories, self.findings)
        self.published_comments = FakePublishedCommentRepo(self.review_runs)
        self.users = FakeUserRepo()
        self.sessions = FakeSessionRepo()
        self.provider_tokens = FakeProviderTokenRepo(self.sessions)
        self.billing = FakeBillingRepo(self.review_runs)

    def _all(self) -> list[Any]:
        return [
            self.repositories,
            self.merge_requests,
            self.review_runs,
            self.review_run_steps,
            self.context_payloads,
            self.findings,
            self.published_comments,
            self.users,
            self.sessions,
            self.provider_tokens,
            self.billing,
        ]

    def snapshot(self) -> list[Any]:
        return [fake.snapshot() for fake in self._all()]

    def restore(self, state: list[Any]) -> None:
        for fake, snap in zip(self._all(), state, strict=True):
            fake.restore(snap)


_MANUAL_RUN_ID = UUID(int=0xA11)


def add_manual_run_with_critical_thread(
    uow: FakeUnitOfWork,
    *,
    merge_request_id: UUID,
    head_sha: str,
    status: ReviewRunStatus,
    at: datetime,
    run_id: UUID = _MANUAL_RUN_ID,
) -> ReviewRun:
    """Ручной прогон на head, опубликовавший открытый тред `critical`: его gate — `failure`.

    Для гонок с check паузы: прогон запущен и дошёл до `status`, пока GitHub принимал POST.
    """
    run = ReviewRun(
        id=run_id,
        merge_request_id=merge_request_id,
        head_sha=head_sha,
        status=status,
        trigger=TriggerSource.MANUAL,
        last_progress_at=at,
        created_at=at,
        updated_at=at,
    )
    uow.review_runs.add(run)
    finding_id = UUID(int=run_id.int + 1)
    uow.findings.add(
        Finding(
            id=finding_id,
            review_run_id=run_id,
            anchor=DiffAnchor(file_path="a.py", side=DiffSide.NEW, new_line=1),
            category=FindingCategory.CORRECTNESS,
            severity=FindingSeverity.CRITICAL,
            message="замечание",
            created_at=at,
            updated_at=at,
        )
    )
    uow.published_comments.add(
        PublishedComment(
            id=UUID(int=run_id.int + 2),
            review_run_id=run_id,
            provider_comment_id=f"manual-{run_id.int}",
            kind=CommentKind.INLINE,
            published_at=at,
            created_at=at,
            updated_at=at,
            finding_id=finding_id,
        )
    )
    # Сводка пишется той же транзакцией, что и inline: по ней видно, что ревью записано.
    uow.published_comments.add(
        PublishedComment(
            id=UUID(int=run_id.int + 3),
            review_run_id=run_id,
            provider_comment_id=f"manual-summary-{run_id.int}",
            kind=CommentKind.SUMMARY,
            published_at=at,
            created_at=at,
            updated_at=at,
        )
    )
    return run
