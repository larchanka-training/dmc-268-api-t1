"""Конфигурация: что обязательно, а что живёт дефолтом.

Профиль — подсистема best-effort без единого вызывающего в проде, поэтому
его адрес Ollama не должен иметь права уронить старт API: окружение без
`OLLAMA_BASE_URL` обязано собираться.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_missing_database_url_fails_naming_the_field(monkeypatch) -> None:
    # CI экспортирует DATABASE_URL в job'е test — проверяем валидацию,
    # а не содержимое окружения гонщика.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)


def test_ollama_base_url_has_a_default(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    settings = Settings(database_url="postgresql+psycopg://t/t", _env_file=None)
    assert settings.ollama_base_url == "http://localhost:11434"
