"""Проверки старта воркера, которые не требуют брокера: промпты и окно модели."""

import contextlib
import io
import json
import logging
import sys
import threading
from collections.abc import Iterator

import pytest

from app.config import Settings
from app.infrastructure.logging import JsonFormatter
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR
from app.infrastructure.telemetry import WORKER_SERVICE
from app.worker import factory
from app.worker.factory import (
    WorkerConfigError,
    check_model_deadline,
    check_sweep_thresholds,
    install_excepthooks,
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


@pytest.fixture(autouse=True)
def _restore_excepthooks() -> Iterator[None]:
    """`run_worker` ставит глобальные excepthook'и — тест возвращает прежние."""
    sys_hook, thread_hook = sys.excepthook, threading.excepthook
    yield
    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


@contextlib.contextmanager
def _json_log(worker_log: logging.Logger) -> Iterator[io.StringIO]:
    """Записи логгера воркера через производственный JSON-форматтер, в память."""
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(JsonFormatter("dmc-268-worker"))
    worker_log.addHandler(handler)
    try:
        yield out
    finally:
        worker_log.removeHandler(handler)


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


def test_run_worker_configures_logging_before_reading_its_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Логи настраиваются первой строкой `run_worker`, до проверок конфигурации:
    `WorkerConfigError` уходит из процесса JSON-записью, которую читает
    коллектор, а не текстом, который он дропает."""
    calls: list[str] = []
    monkeypatch.setattr(factory, "configure_logging", calls.append)

    with pytest.raises(WorkerConfigError, match="LLM_BACKEND"):
        run_worker(_settings())

    assert calls == [WORKER_SERVICE]


def test_unhandled_exceptions_of_both_hooks_go_through_the_logger() -> None:
    """Необработанное исключение — JSON-запись со стектрейсом через логгер
    (`exc_info`), а не дефолтный текстовый дамп интерпретатора: не-JSON
    строку коллектор дропает, и падение воркера осталось бы без диагноза."""
    worker_log = logging.getLogger("app.worker.factory")
    install_excepthooks()

    with _json_log(worker_log) as out:
        try:
            raise WorkerConfigError("LLM_BACKEND не задан: выберите stub или ollama")
        except WorkerConfigError:
            # Как интерпретатор на выходе исключения из `main()` точки входа.
            sys.excepthook(*sys.exc_info())
        try:
            raise RuntimeError("сбой фонового потока")
        except RuntimeError as error:
            threading.excepthook(
                threading.ExceptHookArgs(
                    (type(error), error, error.__traceback__, threading.current_thread())
                )
            )

    main_record, thread_record = [json.loads(line) for line in out.getvalue().splitlines()]
    assert main_record["severity"] == "ERROR"
    assert main_record["exception.type"] == "WorkerConfigError"
    assert "WorkerConfigError: LLM_BACKEND не задан" in main_record["exception.stacktrace"]
    assert thread_record["severity"] == "ERROR"
    assert thread_record["exception.type"] == "RuntimeError"
    assert "RuntimeError: сбой фонового потока" in thread_record["exception.stacktrace"]


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
