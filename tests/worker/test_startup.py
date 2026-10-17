"""Проверки старта воркера, которые не требуют брокера: промпты и окно модели."""

import pytest

from app.config import Settings
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR
from app.worker.factory import WorkerConfigError, load_checked_prompt_texts


def _settings(**overrides: object) -> Settings:
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="test-secret",
        **overrides,  # type: ignore[arg-type]
    )


def test_default_configuration_fits_the_window_and_loads_the_real_prompts() -> None:
    texts = load_checked_prompt_texts(_settings(), DEFAULT_PROMPTS_DIR)
    assert texts.review_system and texts.verifier_system


def test_window_smaller_than_package_and_reserve_stops_the_worker() -> None:
    settings = _settings(
        context_max_tokens=32_000, llm_max_output_tokens=4_096, llm_context_window_tokens=32_768
    )

    with pytest.raises(WorkerConfigError) as error:
        load_checked_prompt_texts(settings, DEFAULT_PROMPTS_DIR)

    message = str(error.value)
    for value in ("32768", "32000", "4096"):
        assert value in message
