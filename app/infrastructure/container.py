"""Composition root.

Единственное место, где порт связывается с адаптером. Выше слоя инфраструктуры
его никто не собирает, поэтому подмена реализации — правка здесь и больше
нигде.
"""

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine

from app.application.ports import EmbeddingGateway, UnitOfWork
from app.config import Settings
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.ollama_embedding_gateway import OllamaEmbeddingGateway


@dataclass(frozen=True, slots=True)
class Container:
    """Всё, что можно передать слою приложения."""

    engine: Engine
    embedding_gateway: EmbeddingGateway

    def unit_of_work(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)


def build_container(settings: Settings) -> Container:
    # pool_pre_ping: соединение, умершее в простое (перезапуск сервера, idle
    # timeout, NAT сбросил поток), обнаруживается при выдаче из пула и
    # заменяется, а не всплывает OperationalError на следующем запросе.
    return Container(
        engine=create_engine(settings.database_url, future=True, pool_pre_ping=True),
        embedding_gateway=OllamaEmbeddingGateway(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            dimension=settings.embedding_dimension,
        ),
    )
