"""Логи сервиса: JSON в stdout со trace-контекстом текущего спана.

Одна точка настройки — `configure_logging(service_name)`: на root ставится
единственный `StreamHandler(sys.stdout)` с JSON-форматтером и
`TraceContextFilter`, логгеры `uvicorn.*` переводятся на этот же хендлер,
чтобы весь stdout контейнера был JSON.

Формат — плоский JSON по модели OTel LogRecord: `timestamp` (RFC 3339, UTC),
`severity`, `message`, `trace_id`/`span_id`/`trace_flags` (lowercase hex, как
в W3C Trace Context), `service.name`, атрибуты из `extra` — на верхнем
уровне. Плоский, а не вложенный: запись читается в Loki без разворачивания и
разбирается `json_parser` коллектора (`trace: {}`, `severity` — имена по
умолчанию). Стектрейс записи с `exc_info` — атрибутом `exception.stacktrace`
(semconv исключений), не вклейкой в текст.

Бизнес-контекст (например `review_run_id`) прикладной код передаёт через
`extra={...}` — теми же именами, что у атрибутов спанов; сам код знает только
`logging.getLogger(__name__)`, никаких обёрток.

Дубль uvicorn «Exception in ASGI application» (сервер пишет его после ответа
на необработанное исключение — когда обработчик `500` уже записал ту же
ошибку с `trace_id` и стектрейсом) отсекается `UvicornDuplicateFilter` на
логгере `uvicorn.error`.

TODO: OTel Logs SDK в Python ещё не Stable (`opentelemetry.sdk._logs` — статус
Development, а `LoggingHandler` оттуда устарел с 1.40.0). Когда станет
Stable — перейти на `LoggingHandler` из `opentelemetry-instrumentation-logging`
(пакет уже в зависимостях) и убрать сбор логов из stdout.
"""

import json
import logging
import sys
import traceback
import typing
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

# Имена полей — как в модели OTel LogRecord и у блоков `json_parser` коллектора.
TIMESTAMP = "timestamp"
SEVERITY = "severity"
MESSAGE = "message"
TRACE_ID = "trace_id"
SPAN_ID = "span_id"
TRACE_FLAGS = "trace_flags"
SERVICE_NAME = "service.name"
STACKTRACE = "exception.stacktrace"

# Запись uvicorn о необработанном исключении; завершающий перевод строки —
# часть сообщения (uvicorn пишет «Exception in ASGI application\n»).
UVICORN_DUPLICATE_MESSAGE = "Exception in ASGI application"

# Атрибуты, которые stdlib кладёт в LogRecord сам: всё остальное в
# `record.__dict__` — из `extra` вызывающего кода.
_RESERVED = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "msecs",
        "message",
        "module",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

# Логгеры uvicorn и его сообщение о исключении в ASGI-приложении.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _rfc3339(seconds: float) -> str:
    """Unix-время → RFC 3339 с зоной UTC: `2026-10-22T12:00:00.500000Z`."""
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat().replace("+00:00", "Z")


def _stacktrace(record: logging.LogRecord) -> str | None:
    """Стектрейс записи: готовый `exc_text`, иначе форматирование `exc_info`."""
    if record.exc_text:
        return record.exc_text.rstrip("\n")
    if record.exc_info is not None:
        return "".join(traceback.format_exception(*record.exc_info)).rstrip("\n")
    return None


def record_to_fields(record: logging.LogRecord, service_name: str) -> dict[str, Any]:
    """Поля JSON-записи по модели OTel LogRecord. Чистая функция над записью.

    `trace_id`/`span_id`/`trace_flags` появляются, только если их положил
    `TraceContextFilter`; вне спана полей нет, запись не теряется. Атрибуты
    из `extra` идут на верхний уровень как есть — фильтр и форматтер не знают
    их имён. `service.name` — ресурс процесса, поэтому приходит аргументом.
    """
    fields: dict[str, Any] = {
        TIMESTAMP: _rfc3339(record.created),
        SEVERITY: record.levelname,
        MESSAGE: record.getMessage(),
        SERVICE_NAME: service_name,
    }
    for name in (TRACE_ID, SPAN_ID, TRACE_FLAGS):
        value = getattr(record, name, None)
        if value is not None:
            fields[name] = value
    stacktrace = _stacktrace(record)
    if stacktrace is not None:
        fields[STACKTRACE] = stacktrace
    for name, value in record.__dict__.items():
        if name not in _RESERVED and name not in (TRACE_ID, SPAN_ID, TRACE_FLAGS):
            fields[name] = value
    return fields


class TraceContextFilter(logging.Filter):
    """Переносит trace-контекст текущего спана в запись лога.

    Стоит на хендлере и выполняется в момент записи, поэтому контекст — того
    потока и задачи, где пишут. Вне спана поля не ставятся: запись уходит без
    них и не теряется.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        context = trace.get_current_span().get_span_context()
        if context.is_valid:
            setattr(record, TRACE_ID, trace.format_trace_id(context.trace_id))
            setattr(record, SPAN_ID, trace.format_span_id(context.span_id))
            setattr(record, TRACE_FLAGS, f"{context.trace_flags:02x}")
        return True


class UvicornDuplicateFilter(logging.Filter):
    """Отсекает дубль uvicorn «Exception in ASGI application».

    Тот же приём, что `AuthQueryRedaction` (`app/api/log_redaction.py`):
    фильтр на логгере uvicorn. Наш обработчик `500` уже записал исключение со
    стектрейсом и `trace_id` (`_internal_error_handler` в `app/api/errors.py`);
    запись uvicorn выходит после закрытия спана запроса и без `trace_id`.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return record.getMessage().rstrip("\n") != UVICORN_DUPLICATE_MESSAGE


# Один экземпляр на процесс: `addFilter` не добавляет тот же объект дважды,
# поэтому повторный вызов `configure_logging` (второй `create_app` в тестах)
# не плодит фильтры.
UVICORN_DUPLICATE_FILTER = UvicornDuplicateFilter()


class JsonFormatter(logging.Formatter):
    """Одна JSON-строка на запись; `service.name` — как resource процесса."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            record_to_fields(record, self.service_name),
            ensure_ascii=False,
            # `extra` может нести любые объекты (UUID, datetime): их JSON-вид — str().
            default=str,
        )


# Хендлер один на процесс и держится ссылкой: повторный вызов не плодит
# записи, а `fileConfig` миграций (снимающий все root-хендлеры) лечится тем же
# вызовом `configure_logging` после себя.
_handler: logging.StreamHandler[typing.TextIO] | None = None


def configure_logging(service_name: str) -> None:
    """Поставить на root единственный JSON-хендлер в stdout; uvicorn — туда же.

    Точки вызова — там же, где настраивается трассировка: фабрика приложения
    (`create_app`, до первого запроса), `run_worker` воркера и `alembic/env.py`
    (до `fileConfig`; он снимает все root-хендлеры, поэтому вызов повторяется
    и после него — вернуть может только наш хендлер).
    """
    global _handler
    root = logging.getLogger()
    if _handler is None:
        _handler = logging.StreamHandler(sys.stdout)
        _handler.addFilter(TraceContextFilter())
    _handler.setFormatter(JsonFormatter(service_name))
    if _handler not in root.handlers:
        root.addHandler(_handler)
    # INFO — рабочие события (ход прогона, access-лог uvicorn); DEBUG не включаем.
    root.setLevel(logging.INFO)
    for name in UVICORN_LOGGERS:
        # Свои хендлеры uvicorn (plain-текст) снимаем: записи уходят
        # propagate-ом на root, в stdout — только JSON.
        uvicorn = logging.getLogger(name)
        uvicorn.handlers.clear()
        uvicorn.propagate = True
    logging.getLogger("uvicorn.error").addFilter(UVICORN_DUPLICATE_FILTER)
