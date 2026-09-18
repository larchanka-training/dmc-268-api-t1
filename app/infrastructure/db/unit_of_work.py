"""The transaction boundary, and the only place a session is created."""

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
    """Opens a session on entry and rolls it back unless `commit` was called."""

    def __init__(self, engine: Engine) -> None:
        self._factory = sessionmaker(bind=engine, future=True, expire_on_commit=False)
        self._session: Session | None = None

    def __enter__(self) -> Self:
        self._session = self._factory()
        session = self._session
        # Typed as the ports, not the concrete adapters: `UnitOfWork` is a
        # `Protocol` with these as plain (mutable) attributes, so structural
        # matching needs the attribute's own type to be the port type, not
        # whatever type mypy would otherwise infer from the adapter literal.
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
