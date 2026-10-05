"""Валидация настроек."""

import pytest
from pydantic import ValidationError

from app.config import Settings

DATABASE_URL = "postgresql+psycopg://t:t@localhost/t"
RABBITMQ_URL = "amqp://guest:guest@localhost//"


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
