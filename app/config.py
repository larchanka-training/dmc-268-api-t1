"""Единственное место, где процесс читает окружение.

Всё, что ниже composition root, получает нужное аргументом
(см. BACKEND_ARCHITECTURE.md, раздел «Конфигурация»).
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация приложения, собираемая один раз на старте.

    Только то, что сегодня кто-то действительно читает. Настройки авторизации,
    rate limit и кэша появятся вместе с кодом, который их использует, не
    раньше.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        description="PostgreSQL DSN, e.g. postgresql+psycopg://user:pass@host/db",
    )
    ollama_base_url: str = Field(
        description="Base URL of the self-hosted Ollama service, e.g. http://ollama:11434",
    )
    embedding_model: str = Field(
        default="nomic-embed-text",
        description="Ollama embedding model used by the repository profile",
    )
    profile_max_chunk_bytes: int = Field(
        default=4096,
        description="Chunks larger than this many bytes are skipped",
    )
    profile_retrieval_top_k: int = Field(
        default=5,
        description="At most this many similar chunks enter the context",
    )
    profile_retrieval_byte_budget: int = Field(
        default=16384,
        description="Total byte budget for the similar-context tier",
    )


def load_settings() -> Settings:
    """Собрать настройки из окружения, громко упав, если какой-то не хватает."""
    return Settings()
