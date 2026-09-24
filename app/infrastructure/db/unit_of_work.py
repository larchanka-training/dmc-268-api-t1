"""Граница транзакции и единственное место, где создаётся сессия."""

from types import TracebackType
from typing import Self

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.application.ports.repositories import (
    ContextPayloadRepo,
    FindingRepo,
    MergeRequestRepo,
    PublishedCommentRepo,
    RepositoryRepo,
    ReviewRunRepo,
)
from app.infrastructure.db.repositories import (
    SqlAlchemyContextPayloadRepo,
    SqlAlchemyFindingRepo,
    SqlAlchemyMergeRequestRepo,
    SqlAlchemyPublishedCommentRepo,
    SqlAlchemyRepositoryRepo,
    SqlAlchemyReviewRunRepo,
)


class SqlAlchemyUnitOfWork:
    """Открывает сессию на входе и откатывает её, если не был вызван `commit`."""

    def __init__(self, engine: Engine) -> None:
        self._factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
        self._session: Session | None = None

    def __enter__(self) -> Self:
        self._session = self._factory()
        session = self._session
        # Типы — порты, а не конкретные адаптеры: `UnitOfWork` — это
        # `Protocol`, где это обычные (изменяемые) атрибуты, поэтому для
        # структурного совпадения тип самого атрибута должен быть типом порта,
        # а не тем, который mypy иначе вывел бы из адаптера справа.
        self.repositories: RepositoryRepo = SqlAlchemyRepositoryRepo(session)
        self.merge_requests: MergeRequestRepo = SqlAlchemyMergeRequestRepo(session)
        self.review_runs: ReviewRunRepo = SqlAlchemyReviewRunRepo(session)
        self.context_payloads: ContextPayloadRepo = SqlAlchemyContextPayloadRepo(session)
        self.findings: FindingRepo = SqlAlchemyFindingRepo(session)
        self.published_comments: PublishedCommentRepo = SqlAlchemyPublishedCommentRepo(
            session
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            self.rollback()
        finally:
            if self._session is not None:
                self._session.close()
                self._session = None

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("unit of work used outside a `with` block")
        return self._session

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        if self._session is not None:
            self._session.rollback()
