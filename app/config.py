"""The one place the process reads its environment.

Everything below the composition root receives what it needs as an argument
(see BACKEND_ARCHITECTURE.md, "Configuration is supplied, never discovered").
"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration, assembled once at startup.

    Only what something actually reads today. Auth, rate limiting and cache
    settings arrive with the code that needs them, not before.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        description="PostgreSQL DSN, e.g. postgresql+psycopg://user:pass@host/db",
    )


def load_settings() -> Settings:
    """Build settings from the environment, failing loudly if one is missing."""
    return Settings()
