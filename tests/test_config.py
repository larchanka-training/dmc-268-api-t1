"""Настройки: секреты не просачиваются в текстовые представления, форма валидируется.

`Settings` попадает в `Container` с рабочим `__repr__`; PEM-ключ и секрет
вебхуков — `SecretStr`, а не `str`, поэтому ни repr настроек, ни трейсбек
с локальными переменными их не напечатают. Пустой секрет вебхуков отклоняется
на старте: HMAC с ключом, который знает каждый, — то же самое, что отсутствие
проверки подписи.
"""

import pytest
from pydantic import ValidationError

from app.config import Settings

DATABASE_URL = "postgresql+psycopg://test:test@localhost/test"
RABBITMQ_URL = "amqp://guest:guest@localhost//"
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\nreal-key-material\n-----END PRIVATE KEY-----"
WEBHOOK_SECRET = "real-webhook-secret-material"


def make_settings() -> Settings:
    return Settings(
        database_url=DATABASE_URL,
        rabbitmq_url=RABBITMQ_URL,
        github_app_id="123456",
        github_app_private_key=PRIVATE_KEY,
        github_webhook_secret=WEBHOOK_SECRET,
    )


def test_repr_hides_both_secrets() -> None:
    rendered = repr(make_settings())
    assert "real-key-material" not in rendered
    assert "real-webhook-secret-material" not in rendered
    assert "BEGIN PRIVATE KEY" not in rendered


def test_str_hides_both_secrets() -> None:
    rendered = str(make_settings())
    assert "real-key-material" not in rendered
    assert "real-webhook-secret-material" not in rendered


def test_secrets_are_readable_through_get_secret_value() -> None:
    """Эндпоинту и composition root нужно само значение — через явный вызов."""
    settings = make_settings()
    assert settings.github_app_private_key.get_secret_value() == PRIVATE_KEY
    assert settings.github_webhook_secret.get_secret_value() == WEBHOOK_SECRET


def test_empty_webhook_secret_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(database_url=DATABASE_URL, rabbitmq_url=RABBITMQ_URL, github_webhook_secret="")


def test_stale_timeout_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        Settings(
            database_url=DATABASE_URL,
            rabbitmq_url=RABBITMQ_URL,
            github_webhook_secret="s",
            stale_run_timeout_seconds=0,
        )
