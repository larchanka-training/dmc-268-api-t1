"""Прогон миграций оставляет трейс: спан `migrate` с SQL-детьми.

Alembic исполняет `env.py` в процессе теста, поэтому спаны видны экспортёру
из `tests/conftest.py`; провайдер там уже стоит, и `configure_telemetry`
его не подменяет — трассировка не меняет результат: база после прогона
стоит на head, как и без неё (change `add-observability-stack`, задача 5.1).
"""

import pytest
from alembic.config import Config
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, StatusCode
from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError

from alembic import command
from app.infrastructure import telemetry

from ..conftest import TEST_DATABASE_URL, requires_db

pytestmark = [pytest.mark.integration, requires_db]


def _config() -> Config:
    assert TEST_DATABASE_URL is not None
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", TEST_DATABASE_URL.replace("%", "%%"))
    return config


def test_an_upgrade_leaves_a_trace_with_sql_children(
    spans: InMemorySpanExporter, migrated
) -> None:
    """Накат даёт один корневой `migrate`, SQL-запросы — его детьми того же trace id."""
    config = _config()
    try:
        # Реальные операторы миграций, а не пустой прогон по базе, уже стоящей
        # на head: откатываем последнюю ревизию и накатываем её под наблюдением.
        command.downgrade(config, "0006")
        spans.clear()
        command.upgrade(config, "head")
        finished: tuple[ReadableSpan, ...] = spans.get_finished_spans()
    finally:
        # Остальные тесты сессии ждут базу на head.
        command.upgrade(config, "head")

    (root,) = [span for span in finished if span.name == "migrate"]
    assert root.parent is None
    sql = [span for span in finished if span.kind is SpanKind.CLIENT]
    assert sql, "SQL-спанов нет: инструментирование движка миграций пропало"
    root_context = root.get_span_context()
    assert root_context is not None
    for span in sql:
        parent = span.parent
        assert parent is not None
        assert parent.span_id == root_context.span_id
        context = span.get_span_context()
        assert context is not None
        assert context.trace_id == root_context.trace_id
    # Результат прежний: после прогона схема стоит на head.
    assert "review_run_steps" in inspect(migrated).get_table_names()


def test_a_failed_run_flushes_its_span_with_an_error(
    spans: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Упавший прогон — тоже сигнал: спан закрыт со статусом ошибки и выгружен.

    `shutdown_tracing` зовётся из `finally`, а исключение наружу не глотается.
    В описании статуса только имя типа: DSN с паролем в трейс не попадает.
    """
    flushed: list[bool] = []
    monkeypatch.setattr(telemetry, "shutdown_tracing", lambda: flushed.append(True))
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url", "postgresql+psycopg://dmc268:secret@localhost:1/none"
    )

    with pytest.raises(OperationalError):
        command.upgrade(config, "head")

    (root,) = [span for span in spans.get_finished_spans() if span.name == "migrate"]
    assert root.status.status_code is StatusCode.ERROR
    assert root.status.description == "OperationalError"
    assert flushed == [True]
