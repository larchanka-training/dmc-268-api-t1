"""Фейковый `UnitOfWork` — для тестов прикладного слоя без БД.

Подменяет порт, не вендора: каждый фейк реализует тот же протокол, что и
`SqlAlchemy*`-адаптер, включая единственное правило, которое адаптер
проверяет сам (`next_status` в `review_runs.update`), чтобы тест на фейке и
тест на реальной базе видели одно и то же поведение.
"""

from collections.abc import Collection
from dataclasses import replace
from datetime import datetime
from types import TracebackType
from typing import Any, Self
from uuid import UUID

from app.application.ports.unit_of_work import UnitOfWork
from app.domain.entities import (
    ContextPayload,
    Finding,
    MergeRequest,
    OpenReviewThread,
    ProviderToken,
    PublishedComment,
    Repository,
    ReviewRun,
    ReviewRunListEntry,
    ReviewRunStep,
    Session,
    User,
)
from app.domain.enums import TERMINAL_STATUSES, CommentKind, Provider
from app.domain.lifecycle import next_status
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

    def update(self, repository: Repository) -> None:
        if repository.id not in self._by_id:
            raise LookupError(f"repository {repository.id} is not stored")
        self._by_id[repository.id] = repository


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

    def list_unfinished(self) -> list[ReviewRun]:
        return [r for r in self._by_id.values() if r.status not in TERMINAL_STATUSES]

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

    def update(self, run: ReviewRun) -> None:
        existing = self._by_id.get(run.id)
        if existing is None:
            raise LookupError(f"review run {run.id} is not stored")
        # Как у SQL-адаптера: проверка перехода — только при смене статуса или
        # для терминальной строки.
        if existing.status in TERMINAL_STATUSES or existing.status != run.status:
            verdict = next_status(existing.status, run.status)
            if not verdict.ok:
                raise ValueError(verdict.error)
        self._by_id[run.id] = run


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
        if step.id not in self._by_id:
            raise LookupError(f"review run step {step.id} is not stored")
        self._by_id[step.id] = step


class FakeContextPayloadRepo:
    def __init__(self) -> None:
        self._items: list[ContextPayload] = []

    def snapshot(self) -> list[ContextPayload]:
        return list(self._items)

    def restore(self, state: list[ContextPayload]) -> None:
        self._items = list(state)

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
        if self._findings is None:
            return []
        runs = {
            r.id: r
            for r in self._review_runs.runs
            if r.merge_request_id == merge_request_id
        }
        threads: list[OpenReviewThread] = []
        for comment in self._items:
            if comment.kind is not CommentKind.INLINE or comment.thread_resolved:
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
                OpenReviewThread(
                    finding=finding,
                    root_provider_comment_id=comment.provider_comment_id,
                    root_published_comment_id=comment.id,
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
        self.users: FakeUserRepo = self._fakes.users
        self.sessions: FakeSessionRepo = self._fakes.sessions
        self.provider_tokens: FakeProviderTokenRepo = self._fakes.provider_tokens
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
        ]

    def snapshot(self) -> list[Any]:
        return [fake.snapshot() for fake in self._all()]

    def restore(self, state: list[Any]) -> None:
        for fake, snap in zip(self._all(), state, strict=True):
            fake.restore(snap)
