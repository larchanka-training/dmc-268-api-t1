"""Старт фабрики приложения: наблюдаемость настраивается до чтения окружения.

`create_app()` без явных настроек читает окружение сам. Падать он обязан уже
с настроенными логами и телеметрией (замечание ревью PR #130): иначе
`ValidationError` pydantic уходит текстом в stderr, коллектор его дропает, и
почему упал контейнер API — в Loki не видно.
"""

import logging

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
