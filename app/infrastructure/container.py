"""Composition root.

Единственное место, где порт связывается с адаптером. Выше слоя инфраструктуры
его никто не собирает, поэтому подмена реализации — правка здесь и больше
нигде.
"""

from dataclasses import dataclass

from sqlalchemy import Engine, create_engine

from app.application.ports import EmbeddingGateway, UnitOfWork
from app.application.profile import IngestProfile, RetrieveSimilarCode
from app.config import Settings
from app.domain.profile import EMBEDDING_DIMENSION, ProfileLimits
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.ollama_embedding_gateway import OllamaEmbeddingGateway


@dataclass(frozen=True, slots=True)
class Container:
    """Всё, что можно передать слою приложения."""

    engine: Engine
    embedding_gateway: EmbeddingGateway
    profile_limits: ProfileLimits
    embedding_model: str

    def unit_of_work(self) -> UnitOfWork:
        return SqlAlchemyUnitOfWork(self.engine)

    def retrieve_similar_code(self, uow: UnitOfWork) -> RetrieveSimilarCode:
        """Use case retrieval для границы транзакции `uow`."""
        return RetrieveSimilarCode(
            self.embedding_gateway,
            uow.code_profile,
            self.embedding_model,
            self.profile_limits,
        )

    def ingest_profile(self, uow: UnitOfWork) -> IngestProfile:
        """Use case накопления для границы транзакции `uow`."""
        return IngestProfile(
            self.embedding_gateway,
            uow.code_profile,
            self.embedding_model,
            self.profile_limits,
        )


def build_container(settings: Settings) -> Container:
    # pool_pre_ping: соединение, умершее в простое (перезапуск сервера, idle
    # timeout, NAT сбросил поток), обнаруживается при выдаче из пула и
    # заменяется, а не всплывает OperationalError на следующем запросе.
    return Container(
        engine=create_engine(settings.database_url, future=True, pool_pre_ping=True),
        embedding_gateway=OllamaEmbeddingGateway(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            dimension=EMBEDDING_DIMENSION,
        ),
        profile_limits=ProfileLimits(
            max_chunk_bytes=settings.profile_max_chunk_bytes,
            retrieval_top_k=settings.profile_retrieval_top_k,
            retrieval_byte_budget=settings.profile_retrieval_byte_budget,
        ),
        embedding_model=settings.embedding_model,
    )
