"""Проверки старта воркера, которые не требуют брокера: промпты и окно модели."""

import contextlib
import io
import json
import logging
import sys
import threading
from collections.abc import Callable, Iterator

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.infrastructure.logging import JsonFormatter, install_excepthooks
from app.infrastructure.prompts import DEFAULT_PROMPTS_DIR
from app.worker import factory
from app.worker.factory import (
    WorkerConfigError,
    check_consumer_timeout,
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


def _loader(settings: Settings) -> Callable[[], Settings]:
    """Загрузчик настроек, каким его ждёт `run_worker`: готовые настройки под рукой."""
    return lambda: settings


def _broken_loader() -> Settings:
    """Загрузчик, как в проде: pydantic отклоняет окружение (`ValidationError`)."""
    return Settings(
        database_url="postgresql+psycopg://test:test@localhost/test",
        rabbitmq_url="amqp://guest:guest@localhost//",
        github_webhook_secret="",
    )


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
        run_worker(_loader(_settings()))


def test_run_worker_configures_logging_before_reading_its_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Логи настраиваются первой строкой `run_worker`, до проверок конфигурации:
    `WorkerConfigError` уходит из процесса JSON-записью, которую читает
    коллектор, а не текстом, который он дропает."""
    calls: list[str] = []
    monkeypatch.setattr(factory, "configure_logging", lambda service: calls.append("logging"))
    monkeypatch.setattr(factory, "install_excepthooks", lambda: calls.append("hooks"))

    with pytest.raises(WorkerConfigError, match="LLM_BACKEND"):
        run_worker(_loader(_settings()))

    assert calls == ["logging", "hooks"]


def test_run_worker_configures_telemetry_before_the_config_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Телеметрия — сразу за логами, до проверок конфигурации: OTLP-хендлер
    уже стоит, когда пишется `WorkerConfigError`, иначе ошибка старта воркера
    до Loki не доходит (замечание ревью PR #130)."""
    calls: list[str] = []
    monkeypatch.setattr(factory, "configure_logging", lambda service: calls.append("logging"))
    monkeypatch.setattr(factory, "configure_telemetry", lambda service: calls.append("telemetry"))
    monkeypatch.setattr(factory, "install_excepthooks", lambda: calls.append("hooks"))

    with pytest.raises(WorkerConfigError, match="LLM_BACKEND"):
        run_worker(_loader(_settings()))

    assert calls == ["logging", "telemetry", "hooks"]


def test_run_worker_reads_the_environment_after_logs_telemetry_and_hooks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Окружение читается последним из стартовых шагов — после логов,
    телеметрии и excepthook'ов: иначе `ValidationError` pydantic выходила бы
    текстом в stderr до настройки логов, и crash-loop воркера был бы невидим
    в Loki (замечание ревью PR #130)."""
    calls: list[str] = []
    monkeypatch.setattr(factory, "configure_logging", lambda service: calls.append("logging"))
    monkeypatch.setattr(factory, "configure_telemetry", lambda service: calls.append("telemetry"))
    monkeypatch.setattr(factory, "install_excepthooks", lambda: calls.append("hooks"))

    def loader() -> Settings:
        calls.append("load")
        return _settings()

    with pytest.raises(WorkerConfigError, match="LLM_BACKEND"):
        run_worker(loader)

    assert calls == ["logging", "telemetry", "hooks", "load"]


def test_a_validation_error_from_the_loader_leaves_a_json_record() -> None:
    """`ValidationError` pydantic из загрузчика настроек доходит до Loki
    JSON-записью: excepthook ставится до чтения окружения (замечание ревью
    PR #130), поэтому падение `python -m app.worker` на кривом окружении
    оставляет в логе стектрейс, а не текст в stderr, который коллектор
    дропает."""
    hook_log = logging.getLogger("app.infrastructure.logging")

    with _json_log(hook_log) as out:
        try:
            run_worker(_broken_loader)
        except ValidationError:
            # Как интерпретатор на выходе исключения из `main()` точки входа.
            sys.excepthook(*sys.exc_info())

    [record] = [json.loads(line) for line in out.getvalue().splitlines()]
    assert record["severity"] == "ERROR"
    assert record["exception.type"] == "ValidationError"
    assert "github_webhook_secret" in record["exception.stacktrace"]


def test_unhandled_exceptions_of_both_hooks_go_through_the_logger() -> None:
    """Необработанное исключение — JSON-запись со стектрейсом через логгер
    (`exc_info`), а не дефолтный текстовый дамп интерпретатора: не-JSON
    строку коллектор дропает, и падение воркера осталось бы без диагноза."""
    hook_log = logging.getLogger("app.infrastructure.logging")
    install_excepthooks()

    with _json_log(hook_log) as out:
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
        run_worker(_loader(_settings(llm_backend="")))


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
        run_worker(_loader(settings))


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
        run_worker(_loader(settings))


def test_default_worst_run_fits_the_consumer_timeout_with_fallback() -> None:
    """Стенд: два вызова по 1200 с с запасным провайдером и минута — меньше двух часов."""
    check_consumer_timeout(
        _settings(
            llm_backend="ollama",
            ollama_model="qwen2.5-coder:7b",
            llm_hosted_fallback=True,
            eurorouter_base_url="https://eurorouter.example/v1",
            eurorouter_model="m",
            eurorouter_api_keys="sk-1",
        )
    )


def test_run_longer_than_the_consumer_timeout_stops_the_worker() -> None:
    """Иначе брокер отдал бы сообщение живого прогона второму воркеру (#223)."""
    settings = _settings(
        llm_backend="ollama",
        ollama_model="qwen2.5-coder:7b",
        llm_timeout_seconds=1800,
        stale_run_timeout_seconds=7200,
    )

    # 2 вызова × 1800 с × 2 попытки + 60 = 7260 с при таймауте 7200 с.
    with pytest.raises(WorkerConfigError, match="7260 .* 7200"):
        check_consumer_timeout(settings)
