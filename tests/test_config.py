"""Конфигурация: что обязательно, а что живёт дефолтом.

Профиль — подсистема best-effort без единого вызывающего в проде, поэтому
его адрес Ollama не должен иметь права уронить старт API: окружение без
`OLLAMA_BASE_URL` обязано собираться.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_missing_database_url_fails_naming_the_field() -> None:
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_ollama_base_url_has_a_default() -> None:
    settings = Settings(database_url="postgresql+psycopg://t/t", _env_file=None)
    assert settings.ollama_base_url == "http://localhost:11434"
