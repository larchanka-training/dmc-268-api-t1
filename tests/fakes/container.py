"""Двойник composition root: настоящий адаптер базы, фейковые VCS и очередь.

Тестам, поднимающим приложение целиком (`create_app`), нужен контейнер, где
`SqlAlchemyUnitOfWork` настоящий, а VCS и очередь — двойники: сеть не нужна.
Подменяется через `app.state.container` — там роуты берут зависимости.
"""

from dataclasses import dataclass

from sqlalchemy import Engine

from app.application.ports import JobQueue, UnitOfWork, VcsGateway
from app.config import Settings
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from .webhooks import FakeQueue, FakeVcs


@dataclass
class FakeContainer:
    engine: Engine
    settings: Settings
    vcs: FakeVcs
    queue: FakeQueue

    def unit_of_work(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)

    def vcs_gateway(self) -> VcsGateway:
        return self.vcs

    def job_queue(self) -> JobQueue:
        return self.queue
