"""Composition root.

The single place a port is bound to an adapter. Nothing above the
infrastructure layer constructs one, so swapping an implementation is a change
here and nowhere else.
"""

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine

from app.application.ports import UnitOfWork
from app.config import Settings
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork


@dataclass(frozen=True, slots=True)
class Container:
    """Everything the application layer can be handed."""

    engine: Engine

    def unit_of_work(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)


def build_container(settings: Settings) -> Container:
    return Container(engine=create_engine(settings.database_url, future=True))
