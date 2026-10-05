"""Composition root.

Единственное место, где порт связывается с адаптером. Выше слоя инфраструктуры
его никто не собирает, поэтому подмена реализации — правка здесь и больше
нигде.
"""

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine

from app.application.ports import UnitOfWork
from app.application.ports.job_queue import JobQueue
from app.application.ports.llm_gateway import LlmGateway
from app.config import Settings
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.llm.stub import StubLlmGateway
from app.infrastructure.queue.rabbitmq import RabbitMQJobQueue


@dataclass(frozen=True, slots=True)
class Container:
    """Всё, что можно передать слою приложения."""

    engine: Engine
    # Один экземпляр на процесс, а не фабрика: `RabbitMQJobQueue` сам держит
    # переиспользуемое AMQP-соединение, и это работает, только если каждый
    # запрос получает один и тот же объект, а не новый на каждый вызов
    # `job_queue()`.
    _job_queue: JobQueue

    def unit_of_work(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)

    def job_queue(self) -> JobQueue:
        return self._job_queue

    def llm_gateway(self) -> LlmGateway:
        # Заглушка: реальный транспорт к Ollama — отдельная задача, контракт
        # порта уже готов её принять (см. app/application/ports/llm_gateway.py).
        return StubLlmGateway()


def build_container(settings: Settings) -> Container:
    # pool_pre_ping: соединение, умершее в простое (перезапуск сервера, idle
    # timeout, NAT сбросил поток), обнаруживается при выдаче из пула и
    # заменяется, а не всплывает OperationalError на следующем запросе.
    return Container(
        engine=create_engine(settings.database_url, future=True, pool_pre_ping=True),
        _job_queue=RabbitMQJobQueue(settings.rabbitmq_url),
    )
