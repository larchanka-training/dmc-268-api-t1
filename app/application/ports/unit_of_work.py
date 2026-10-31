"""Граница транзакции.

Use case принимает unit of work, через него добирается до репозиториев и
делает один коммит. Ниже этой строки никто не знает, что такое сессия.

`ActiveRunConflict` — отказ коммита из-за гонки параллельных транзакций:
двух доставок одного PR (уникальные индексы `uq_review_runs_one_active_per_commit`
и `uq_merge_requests_repo_number`) или двух прогонов, подобравших одно ревью
упавшего (`uq_published_comments_provider_comment_id`). Текст называет ограничение. Исключение определено в порту, а не
заимствовано у вендора хранилища: use case ловит его, не зная SQLAlchemy.
"""

from types import TracebackType
from typing import Protocol, Self

from app.application.ports.repositories import (
    ContextPayloadRepo,
    FindingRepo,
    MergeRequestRepo,
    ProviderTokenRepo,
    PublishedCommentRepo,
    RepositoryRepo,
    ReviewRunRepo,
    ReviewRunStepRepo,
    SessionRepo,
    UserRepo,
)


class ActiveRunConflict(RuntimeError):
    """Параллельная доставка того же PR закоммитила записи раньше этой
    транзакции; повторная доставка проиграла гонку индексов. Так же - когда
    параллельный прогон раньше записал те же комментарии ревью упавшего (#222)."""


class UnitOfWork(Protocol):
    repositories: RepositoryRepo
    merge_requests: MergeRequestRepo
    review_runs: ReviewRunRepo
    review_run_steps: ReviewRunStepRepo
    context_payloads: ContextPayloadRepo
    findings: FindingRepo
    published_comments: PublishedCommentRepo
    users: UserRepo
    sessions: SessionRepo
    provider_tokens: ProviderTokenRepo

    def __enter__(self) -> Self: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...

    def rollback(self) -> None: ...
