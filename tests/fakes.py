"""Фейковые двойники портов и канала брокера, общие для тестов.

Это не моки: двойники хранят сущности в словарях и записывают вызовы, поэтому
тесты сверяют исход с точными записями и задачами. Ни базы, ни брокера, ни
сети — порты существуют именно для этого.
"""

import datetime as dt
import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self
from uuid import UUID, uuid4

import pika

from app.application.ports import UnitOfWork
from app.domain.entities import Hunk, PRMetadata, Repository, ReviewJob
from app.domain.enums import TERMINAL_STATUSES, MergeRequestState, Provider
from app.domain.vcs_errors import VcsError

FIXTURES = Path(__file__).parent / "fixtures"
NOW = dt.datetime(2026, 9, 29, 12, 0, tzinfo=dt.UTC)

REGISTERED_PROVIDER_ID = "923478362"
INSTALLATION_ID = 512804923
REPO_FULL_NAME = "larchanka-training/dmc-268-api-t1"
DIFF = (
    "diff --git a/app/main.py b/app/main.py\n"
    "--- a/app/main.py\n+++ b/app/main.py\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)


def webhook_payload(action: str = "opened") -> dict[str, Any]:
    """Записанный payload GitHub; для прочих действий — тело `opened` с заменой."""
    name = (
        "github_webhook_synchronize.json"
        if action == "synchronize"
        else "github_webhook_opened.json"
    )
    raw: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    raw["action"] = action
    return raw


def pr_metadata(base_sha: str = "fedcba0987654321fedcba0987654321fedcba09") -> PRMetadata:
    """Свежие метаданные VCS.

    Заголовок, автор и ветки нарочно отличаются от payload'а вебхука: так
    тест видит, что в `MergeRequest` ложится версия из метаданных, а не
    устаревшая копия из доставленного тела.
    """
    return PRMetadata(
        number=6,
        head_sha="a1b2c3d4e5f6789012345678abcdef0123456789",
        base_sha=base_sha,
        title="feat: метаданные из VCS свежее payload'а",
        author="grinv",
        source_branch="feat/webhook-intake-rebased",
        target_branch="main",
        state=MergeRequestState.OPEN,
    )


def a_repository() -> Repository:
    return Repository(
        id=uuid4(),
        provider=Provider.GITHUB,
        provider_id=REGISTERED_PROVIDER_ID,
        full_name=REPO_FULL_NAME,
        default_branch="develop",
        auto_review_enabled=True,
        created_at=NOW,
        updated_at=NOW,
    )


@dataclass
class FakeRepositories:
    stored: dict[tuple[Provider, str], Repository] = field(default_factory=dict)

    def get(self, repository_id: UUID) -> Repository | None:
        return next((r for r in self.stored.values() if r.id == repository_id), None)

    def find_by_provider(self, provider: Provider, provider_id: str) -> Repository | None:
        return self.stored.get((provider, provider_id))

    def add(self, repository: Repository) -> None:
        self.stored[(repository.provider, repository.provider_id)] = repository

    def update(self, repository: Repository) -> None:
        self.add(repository)


@dataclass
class FakeMergeRequests:
    by_number: dict[tuple[UUID, int], Any] = field(default_factory=dict)
    added: list[Any] = field(default_factory=list)
    updated: list[Any] = field(default_factory=list)

    def get(self, merge_request_id: UUID) -> Any | None:
        return next((m for m in self.by_number.values() if m.id == merge_request_id), None)

    def find_by_number(self, repository_id: UUID, number: int) -> Any | None:
        return self.by_number.get((repository_id, number))

    def add(self, merge_request: Any) -> None:
        self.added.append(merge_request)
        self.by_number[(merge_request.repository_id, merge_request.number)] = merge_request

    def update(self, merge_request: Any) -> None:
        self.updated.append(merge_request)
        self.add(merge_request)


@dataclass
class FakeReviewRuns:
    runs: list[Any] = field(default_factory=list)
    added: list[Any] = field(default_factory=list)

    def get(self, run_id: UUID) -> Any | None:
        return next((r for r in self.runs if r.id == run_id), None)

    def find_active(self, merge_request_id: UUID, head_sha: str) -> Any | None:
        # Тот же фильтр, что у `SqlAlchemyReviewRunRepo.find_active`:
        # завершённый прогон на том же коммите не блокирует новый.
        return next(
            (
                r
                for r in self.runs
                if r.merge_request_id == merge_request_id
                and r.head_sha == head_sha
                and r.status not in TERMINAL_STATUSES
            ),
            None,
        )

    def list_unfinished(self) -> list[Any]:
        return [r for r in self.runs if r.status not in TERMINAL_STATUSES]

    def add(self, run: Any) -> None:
        self.added.append(run)
        self.runs.append(run)

    def update(self, run: Any) -> None:
        self.runs = [run if r.id == run.id else r for r in self.runs]


@dataclass
class FakeContextPayloads:
    stored: list[Any] = field(default_factory=list)

    def list_for_run(self, review_run_id: UUID) -> list[Any]:
        return [p for p in self.stored if p.review_run_id == review_run_id]

    def find_by_digest(self, content_sha256: str) -> Any | None:
        return next(
            (p for p in self.stored if p.content_sha256 == content_sha256), None
        )

    def add(self, payload: Any) -> None:
        self.stored.append(payload)


@dataclass
class FakeFindings:
    stored: list[Any] = field(default_factory=list)

    def list_for_run(self, review_run_id: UUID) -> list[Any]:
        return [f for f in self.stored if f.review_run_id == review_run_id]

    def add(self, finding: Any) -> None:
        self.stored.append(finding)

    def add_validated(
        self, finding: Any, hunks: Iterable[Hunk], now: dt.datetime
    ) -> None:
        self.stored.append(finding)


@dataclass
class FakePublishedComments:
    stored: list[Any] = field(default_factory=list)

    def list_for_run(self, review_run_id: UUID) -> list[Any]:
        return [c for c in self.stored if c.review_run_id == review_run_id]

    def add(self, comment: Any) -> None:
        self.stored.append(comment)


@dataclass
class FakeUow(UnitOfWork):
    """Явное наследование протокола: изменяемые атрибуты `UnitOfWork` mypy
    проверяет инвариантно, поэтому конкретные типы фейков совместимы с ним
    только номинально, через базу."""

    repositories: FakeRepositories = field(default_factory=FakeRepositories)
    merge_requests: FakeMergeRequests = field(default_factory=FakeMergeRequests)
    review_runs: FakeReviewRuns = field(default_factory=FakeReviewRuns)
    context_payloads: FakeContextPayloads = field(default_factory=FakeContextPayloads)
    findings: FakeFindings = field(default_factory=FakeFindings)
    published_comments: FakePublishedComments = field(
        default_factory=FakePublishedComments
    )
    commits: int = 0
    rollbacks: int = 0
    commit_error: Exception | None = None
    # Сколько прогонов закоммичено к началу текущей транзакции: rollback
    # убирает добавленные после этого, как это сделал бы настоящий UoW.
    _committed_runs: int = 0

    def __enter__(self) -> Self:
        self._committed_runs = len(self.review_runs.runs)
        return self

    def __exit__(self, *args: object) -> None:
        # Как `SqlAlchemyUnitOfWork`: выход из `with` без коммита откатывает
        # транзакцию — иначе фейк маскирует молчаливую потерю записей.
        self.rollback()

    def commit(self) -> None:
        self.commits += 1
        if self.commit_error is not None:
            raise self.commit_error
        self._committed_runs = len(self.review_runs.runs)

    def rollback(self) -> None:
        self.rollbacks += 1
        runs = self.review_runs
        del runs.runs[self._committed_runs :]
        del runs.added[self._committed_runs :]


@dataclass
class FakeVcs:
    diff: str = DIFF
    meta: PRMetadata = field(default_factory=pr_metadata)
    error: VcsError | None = None
    calls: list[tuple[str, str, int, int]] = field(default_factory=list)

    def fetch_diff(self, repo_full_name: str, pr_number: int, installation_id: int) -> str:
        self.calls.append(("diff", repo_full_name, pr_number, installation_id))
        if self.error is not None:
            raise self.error
        return self.diff

    def fetch_pr_metadata(
        self, repo_full_name: str, pr_number: int, installation_id: int
    ) -> PRMetadata:
        self.calls.append(("metadata", repo_full_name, pr_number, installation_id))
        if self.error is not None:
            raise self.error
        return self.meta


@dataclass
class FakeQueue:
    jobs: list[ReviewJob] = field(default_factory=list)

    def enqueue(self, job: ReviewJob) -> None:
        self.jobs.append(job)


@dataclass
class Published:
    """Одна публикация в брокер: обмен, маршрут, тело и свойства AMQP."""

    exchange: str
    routing_key: str
    body: bytes
    properties: pika.BasicProperties


class FakeChannel:
    """Двойник pika-канала: только запись вызовов, без брокера."""

    def __init__(self) -> None:
        self.declared: list[dict[str, Any]] = []
        self.published: list[Published] = []

    def queue_declare(
        self,
        queue: str,
        durable: bool = False,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        self.declared.append(
            {"queue": queue, "durable": durable, "arguments": arguments}
        )

    def basic_publish(
        self,
        exchange: str,
        routing_key: str,
        body: bytes,
        properties: pika.BasicProperties | None = None,
    ) -> None:
        assert properties is not None
        self.published.append(Published(exchange, routing_key, body, properties))
