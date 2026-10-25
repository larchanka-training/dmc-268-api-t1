"""Старт фабрики приложения: наблюдаемость настраивается до чтения окружения.

`create_app()` без явных настроек читает окружение сам. Падать он обязан уже
с настроенными логами и телеметрией (замечание ревью PR #130): иначе
`ValidationError` pydantic уходит текстом в stderr, коллектор его дропает, и
почему упал контейнер API — в Loki не видно.
"""

import contextlib
import io
import json
import logging
import sys
from collections.abc import Iterator

import pytest
from pydantic import ValidationError

from app.api.factory import create_app
from app.infrastructure import logging as app_logging
from app.infrastructure import telemetry
from app.infrastructure.logging import JsonFormatter

REQUIRED_ENV = {
    "DATABASE_URL": "postgresql+psycopg://test:test@localhost/test",
    "RABBITMQ_URL": "amqp://guest:guest@localhost//",
}


@contextlib.contextmanager
def _json_log(log: logging.Logger) -> Iterator[io.StringIO]:
    """Записи логгера через производственный JSON-форматтер, в память."""
    out = io.StringIO()
    handler = logging.StreamHandler(out)
    handler.setFormatter(JsonFormatter("dmc-268-api"))
    log.addHandler(handler)
    try:
        yield out
    finally:
        log.removeHandler(handler)


def test_create_app_reads_the_environment_after_logs_and_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Окружение читается после `configure_logging` и `configure_telemetry`:
    к моменту `ValidationError` JSON-хендлер и провайдеры уже стоят, и запись
    о падении старта доходит до Loki."""
    calls: list[str] = []
    monkeypatch.setattr(app_logging, "configure_logging", lambda service: calls.append("logging"))
    monkeypatch.setattr(telemetry, "configure_telemetry", lambda service: calls.append("telemetry"))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")  # pydantic отклоняет: min_length=1
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match="github_webhook_secret"):
        create_app()

    assert calls == ["logging", "telemetry"]


def test_a_broken_environment_fails_with_the_json_handler_on_the_root_logger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Реальный поток без подмен: `create_app()` на кривом окружении падает
    `ValidationError`, и JSON-хендлер к этому моменту уже на root-логгере.
    Хендлер снимается перед вызовом: иначе проверка проходила бы и со старым
    порядком — хендлер ставил бы кто-то из предыдущих тестов."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]:
        root.removeHandler(handler)

    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")
    for name, value in REQUIRED_ENV.items():
        monkeypatch.setenv(name, value)

    with pytest.raises(ValidationError, match="github_webhook_secret"):
        create_app()

    assert [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)], (
        "JSON-хендлер не на root-логгере в момент падения чтения окружения: "
        "настройки читаются раньше, чем настроены логи"
    )


def test_a_validation_error_from_the_environment_leaves_a_json_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ValidationError` из чтения окружения доходит до Loki JSON-записью:
    excepthook ставится раньше `load_settings` (замечание ревью PR #130),
    поэтому падение `uvicorn app.main:app` на кривом окружении оставляет запись
    с `exception.type` и стектрейсом, а не текст в stderr, который коллектор
    дропает."""
    original_hook = sys.excepthook
    # Запись хука идёт через логгер модуля, где хук теперь живёт.
    hook_log = logging.getLogger("app.infrastructure.logging")
    try:
        with _json_log(hook_log) as out:
            monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "")  # pydantic отклоняет: min_length=1
            for name, value in REQUIRED_ENV.items():
                monkeypatch.setenv(name, value)
            try:
                create_app()
            except ValidationError:
                # Как интерпретатор на выходе исключения из точки входа.
                sys.excepthook(*sys.exc_info())
    finally:
        sys.excepthook = original_hook

    [record] = [json.loads(line) for line in out.getvalue().splitlines()]
    assert record["severity"] == "ERROR"
    assert record["exception.type"] == "ValidationError"
    assert "github_webhook_secret" in record["exception.stacktrace"]
