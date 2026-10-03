"""Граница транзакции.

Use case принимает unit of work, через него добирается до репозиториев и
делает один коммит. Ниже этой строки никто не знает, что такое сессия.

`ActiveRunConflict` — отказ коммита из-за гонки двух доставок одного PR:
гонку страхуют уникальные индексы `uq_review_runs_one_active_per_commit`
и `uq_merge_requests_repo_number`. Исключение определено в порту, а не
заимствовано у вендора хранилища: use case ловит его, не зная SQLAlchemy.
"""

from types import TracebackType
from typing import Protocol, Self

from app.application.ports.repositories import (
    ContextPayloadRepo,
    FindingRepo,
    MergeRequestRepo,
    PublishedCommentRepo,
    RepositoryRepo,
    ReviewRunRepo,
)


class ActiveRunConflict(RuntimeError):
    """Параллельная доставка того же PR закоммитила записи раньше этой
    транзакции; повторная доставка проиграла гонку индексов."""


class UnitOfWork(Protocol):
    repositories: RepositoryRepo
    merge_requests: MergeRequestRepo
    review_runs: ReviewRunRepo
    context_payloads: ContextPayloadRepo
    findings: FindingRepo
    published_comments: PublishedCommentRepo

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
