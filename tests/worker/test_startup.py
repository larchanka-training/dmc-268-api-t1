"""Проверки старта воркера, которые не требуют брокера: промпты и окно модели."""

import pytest

from app.config import Settings
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR
from app.worker.factory import (
    WorkerConfigError,
    check_model_deadline,
    check_sweep_thresholds,
    load_checked_prompt_texts,
    run_worker,
)


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


def test_worker_without_a_chosen_model_backend_does_not_start() -> None:
    # Брокер по адресу из настроек недоступен: тест проходит, только если
    # проверка срабатывает раньше подключения.
    with pytest.raises(WorkerConfigError, match="LLM_BACKEND"):
        run_worker(_settings())


def test_empty_model_backend_from_compose_does_not_start_the_worker() -> None:
    with pytest.raises(WorkerConfigError, match="LLM_BACKEND"):
        run_worker(_settings(llm_backend=""))


def test_default_model_timeouts_fit_under_the_stale_run_threshold() -> None:
    check_model_deadline(
        _settings(
            llm_backend="ollama",
            ollama_model="qwen2.5-coder:7b",
            llm_hosted_fallback=True,
            eurorouter_base_url="https://eurorouter.example/v1",
            eurorouter_model="m",
            eurorouter_api_keys="sk-1",
        )
    )


def test_model_call_longer_than_the_stale_run_threshold_stops_the_worker() -> None:
    settings = _settings(
        llm_backend="ollama",
        ollama_model="qwen2.5-coder:7b",
        llm_hosted_fallback=True,
        eurorouter_base_url="https://eurorouter.example/v1",
        eurorouter_model="m",
        eurorouter_api_keys="sk-1",
        llm_timeout_seconds=600,
        stale_run_timeout_seconds=1800,
    )

    with pytest.raises(WorkerConfigError, match="2400 .* 1800"):
        check_model_deadline(settings)

    # Брокер недоступен: проверка срабатывает раньше подключения.
    with pytest.raises(WorkerConfigError, match="2400"):
        run_worker(settings)


def test_default_sweep_thresholds_pass_the_startup_check() -> None:
    check_sweep_thresholds(_settings())


@pytest.mark.parametrize("queued", [1800, 600])
def test_queued_threshold_not_above_the_stale_one_stops_the_worker(queued: int) -> None:
    settings = _settings(
        llm_backend="stub", stale_run_timeout_seconds=1800, queued_run_timeout_seconds=queued
    )

    with pytest.raises(
        WorkerConfigError, match=rf"{queued} с \(QUEUED_RUN_TIMEOUT_SECONDS\).* 1800 с"
    ):
        check_sweep_thresholds(settings)

    # Брокер недоступен: проверка срабатывает раньше подключения.
    with pytest.raises(WorkerConfigError, match="QUEUED_RUN_TIMEOUT_SECONDS"):
        run_worker(settings)
