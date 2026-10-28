"""Двойник composition root: настоящий адаптер базы, фейковые VCS и очередь.

Тестам, поднимающим приложение целиком (`create_app`), нужен контейнер, где
`SqlAlchemyUnitOfWork` настоящий, а VCS и очередь — двойники: сеть не нужна.
Подменяется через `app.state.container` — там роуты берут зависимости.
"""

from dataclasses import dataclass, field

from sqlalchemy import Engine

from app.application.ports import JobQueue, UnitOfWork, VcsGateway
from app.config import Settings
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork

from .run_outcome import RecordingRunOutcome
from .webhooks import FakeQueue, FakeVcs


@dataclass
class FakeContainer:
    engine: Engine
    settings: Settings
    vcs: FakeVcs
    queue: FakeQueue
    # Счётчик исходов — двойник: вебхук-роут берёт его у контейнера,
    # тест при необходимости сверяет записанные статусы.
    outcome: RecordingRunOutcome = field(default_factory=RecordingRunOutcome)

    def unit_of_work(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)

    def vcs_gateway(self) -> VcsGateway:
        return self.vcs

    def job_queue(self) -> JobQueue:
        return self.queue

    def run_outcome(self) -> RecordingRunOutcome:
        return self.outcome
