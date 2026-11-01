"""Логи сервиса: JSON в stdout со trace-контекстом текущего спана.

Одна точка настройки — `configure_logging(service_name)`: на root ставится
единственный `StreamHandler(sys.stdout)` с JSON-форматтером и
`TraceContextFilter`, логгеры `uvicorn.*` переводятся на этот же хендлер,
чтобы весь stdout контейнера был JSON. Миграции пишут тот же JSON в stderr:
их stdout — результат команды alembic, его читают скрипты деплоя.

Формат — плоский JSON по модели OTel LogRecord: `timestamp` (RFC 3339, UTC),
`severity`, `message`, `logger` (имя логгера), `trace_id`/`span_id`/`trace_flags`
(lowercase hex, как в W3C Trace Context), `service.name`, атрибуты из `extra` —
на верхнем уровне. Плоский, а не вложенный: запись читается в Loki без
разворачивания и разбирается `json_parser` коллектора (`trace: {}`, `severity`
— имена по умолчанию). Стектрейс записи с `exc_info` — атрибутом
`exception.stacktrace`, тип исключения — `exception.type` (semconv исключений),
не вклейкой в текст: редакция коллектора гасит тело такой записи, и тип
остаётся её единственным диагнозом. Служебные поля (`severity`, `timestamp`,
`service.name`, `logger`) `extra` перетереть не может.

Бизнес-контекст (например `review_run_id`) прикладной код передаёт через
`extra={...}` или через `review_run_id_scope` и `simulation_id_scope` -
контекст, который фильтр хендлера кладёт в записи сам (обработчик воркера
ставит его на всё сообщение). Имена - те же, что у атрибутов спанов; сам код знает только
`logging.getLogger(__name__)`, никаких обёрток.

Дубль uvicorn «Exception in ASGI application» (сервер пишет его после ответа
на необработанное исключение — когда обработчик `500` уже записал ту же
ошибку с `trace_id` и стектрейсом) отсекается `UvicornDuplicateFilter` на
логгере `uvicorn.error`.

Необработанные исключения процесса — тоже записи этого лога: `install_excepthooks`
ставит `sys.excepthook` и `threading.excepthook`, пишущие через логгер этого
модуля. Хук ставит любой вход — `create_app` API и `run_worker` воркера, —
сразу после настройки логов, поэтому падение процесса доходит до Loki.

TODO: OTel Logs SDK в Python ещё не Stable (`opentelemetry.sdk._logs` — статус
Development, а `LoggingHandler` оттуда устарел с 1.40.0). Когда станет
Stable — перейти на `LoggingHandler` из `opentelemetry-instrumentation-logging`
и убрать сбор логов из stdout.
"""

import contextvars
import json
import logging
import sys
import threading
import traceback
import typing
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from opentelemetry import trace

# Логгер модуля: через него пишут excepthook'и (`install_excepthooks`) —
# необработанное исключение любого потока выходит JSON-записью отсюда.
logger = logging.getLogger(__name__)

# Имена полей — как в модели OTel LogRecord и у блоков `json_parser` коллектора.
TIMESTAMP = "timestamp"
SEVERITY = "severity"
MESSAGE = "message"
LOGGER = "logger"
TRACE_ID = "trace_id"
SPAN_ID = "span_id"
TRACE_FLAGS = "trace_flags"
SERVICE_NAME = "service.name"
STACKTRACE = "exception.stacktrace"
EXCEPTION_TYPE = "exception.type"

# Имя атрибута бизнес-контекста — то же, что у атрибута спанов (`REVIEW_RUN_ID`
# в `app/infrastructure/telemetry.py`): одна сущность — одно имя везде (правило
# «Трассировка»). Константа локальная, а не импорт: настройке логов связь с
# телеметрией импортом не нужна — имя фиксирует правило спеки.
REVIEW_RUN_ID = "review_run_id"

# Контекст обрабатываемого сообщения: ставит обработчик воркера
# (`review_run_id_scope`), читает фильтр хендлера в момент записи — у записи
# того потока и задачи, где её написали.
_review_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "review_run_id", default=None
)


@contextmanager
def review_run_id_scope(review_run_id: str | None) -> Iterator[None]:
    """Задать `review_run_id` записям лога внутри блока.

    Контекст протягивается вглубь вызовов без правки их сигнатур: всё, что
    пайплайн пишет во время обработки сообщения, уходит с атрибутом прогона и
    ищется в Loki по полю, а не по тексту. Выход из блока возвращает прежний
    контекст (токен `ContextVar`), поэтому вложенность и повторный вход
    безопасны.
    """
    token = _review_run_id.set(review_run_id)
    try:
        yield
    finally:
        _review_run_id.reset(token)


class ReviewRunIdFilter(logging.Filter):
    """Кладёт `review_run_id` из контекста в запись; вне контекста поля нет.

    Стоит на хендлере рядом с `TraceContextFilter`: бизнес-контекст — атрибут
    записи теми же именами, что у атрибутов спанов, не строка в тексте
    сообщения (правило «Логи»). Запись вне контекста уходит без поля и не
    теряется.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        value = _review_run_id.get()
        if value is not None:
            setattr(record, REVIEW_RUN_ID, value)
        return True


# То же для симуляции песочницы правил: атрибут записи, а не строка в тексте.
SIMULATION_ID = "simulation_id"
_simulation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "simulation_id", default=None
)


@contextmanager
def simulation_id_scope(simulation_id: str | None) -> Iterator[None]:
    """Задать `simulation_id` записям лога внутри блока - как `review_run_id_scope`."""
    token = _simulation_id.set(simulation_id)
    try:
        yield
    finally:
        _simulation_id.reset(token)


class SimulationIdFilter(logging.Filter):
    """Кладёт `simulation_id` из контекста в запись; вне контекста поля нет."""

    def filter(self, record: logging.LogRecord) -> bool:
        value = _simulation_id.get()
        if value is not None:
            setattr(record, SIMULATION_ID, value)
        return True

# Запись uvicorn о необработанном исключении; завершающий перевод строки —
# часть сообщения (uvicorn пишет «Exception in ASGI application\n»).
UVICORN_DUPLICATE_MESSAGE = "Exception in ASGI application"

# Атрибуты, которые stdlib кладёт в LogRecord сам, строятся из пустой записи:
# новый атрибут LogRecord из будущей версии Python (как `taskName` в 3.12)
# попадает сюда автоматически, а не уходит в JSON как «extra» (замечание
# ревью PR #130). `message` и `asctime` stdlib ставит позже, при форматировании.
# Остальное в списке — имена полей JSON, которые собирает сам форматтер:
# `extra` перетереть их не может — подделка `severity` не меняет уровень
# записи, `timestamp`, `service.name`, `logger`, `exception.*` остаются своими.
_RESERVED = frozenset(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
    TIMESTAMP,
    SEVERITY,
    MESSAGE,
    LOGGER,
    SERVICE_NAME,
    STACKTRACE,
    EXCEPTION_TYPE,
}

# Логгеры uvicorn и его сообщение о исключении в ASGI-приложении.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")

# Логгеры HTTP-клиентов: на INFO httpx пишет «HTTP Request: <метод> <URL>»
# о каждом исходящем запросе — имя приватного репозитория, пути файлов и
# токены в query уходили бы в лог открытым текстом (замечание ревью
# PR #130); httpcore на DEBUG пишет хосты и пути соединений. WARNING
# оставляет клиентам записи о настоящих сбоях.
HTTP_CLIENT_LOGGERS = ("httpx", "httpcore")


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


def _exception_type(record: logging.LogRecord) -> str | None:
    """Тип исключения записи — qualname класса из `exc_info`.

    Редакция коллектора гасит `exception.message`, `exception.stacktrace` и
    тело записи, несущей исключение, — `exception.type` остаётся её
    единственным диагнозом (в трейсах тип сохраняется по той же спеке).
    """
    exc_type = record.exc_info[0] if record.exc_info else None
    if exc_type is None:
        return None
    return exc_type.__qualname__


def record_to_fields(record: logging.LogRecord, service_name: str) -> dict[str, Any]:
    """Поля JSON-записи по модели OTel LogRecord. Чистая функция над записью.

    `trace_id`/`span_id`/`trace_flags` появляются, только если их положил
    `TraceContextFilter`; вне спана полей нет, запись не теряется. Атрибуты
    из `extra` идут на верхний уровень как есть — фильтр и форматтер не знают
    их имён, а служебные поля записи перетереть не дают (`_RESERVED`).
    Коллизия `extra` с trace-полем решается в пользу настоящего контекста:
    фильтры выполняются после помещения `extra` в запись и перезаписывают
    её значение; вне спана значение из `extra` доходит до JSON, а не пропадает
    молча. `service.name` — ресурс процесса, поэтому приходит аргументом.
    """
    fields: dict[str, Any] = {
        TIMESTAMP: _rfc3339(record.created),
        SEVERITY: record.levelname,
        MESSAGE: record.getMessage(),
        LOGGER: record.name,
        SERVICE_NAME: service_name,
    }
    for name in (TRACE_ID, SPAN_ID, TRACE_FLAGS):
        value = getattr(record, name, None)
        if value is not None:
            fields[name] = value
    stacktrace = _stacktrace(record)
    if stacktrace is not None:
        fields[STACKTRACE] = stacktrace
    exception_type = _exception_type(record)
    if exception_type is not None:
        fields[EXCEPTION_TYPE] = exception_type
    for name, value in record.__dict__.items():
        if name not in _RESERVED:
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

    Тот же приём, что `AccessQueryRedaction` (`app/api/log_redaction.py`):
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


def configure_logging(service_name: str, stream: typing.TextIO | None = None) -> None:
    """Поставить на root единственный JSON-хендлер в stdout; uvicorn — туда же.

    `stream` — куда писать вместо stdout. Его передают миграции
    (`alembic/env.py`, `sys.stderr`): stdout alembic CLI — результат команды
    (`current`, `heads`), его читают скрипты деплоя. Без `stream` — stdout.

    Логгеры HTTP-клиентов (`HTTP_CLIENT_LOGGERS`) переводятся на WARNING.
    Точки вызова — там же, где настраивается трассировка: фабрика приложения
    (`create_app`, до первого запроса), `run_worker` воркера и `alembic/env.py`
    (до `fileConfig`; он снимает все root-хендлеры, поэтому вызов повторяется
    и после него — вернуть может только наш хендлер).
    """
    global _handler
    root = logging.getLogger()
    if _handler is None:
        _handler = logging.StreamHandler()
        _handler.addFilter(TraceContextFilter())
        _handler.addFilter(ReviewRunIdFilter())
        _handler.addFilter(SimulationIdFilter())
    # Поток — на каждом вызове: без `stream` — stdout, а не поток прошлого вызова.
    _handler.setStream(sys.stdout if stream is None else stream)
    _handler.setFormatter(JsonFormatter(service_name))
    if _handler not in root.handlers:
        root.addHandler(_handler)
    # INFO — рабочие события (ход прогона, access-лог uvicorn); DEBUG не включаем.
    root.setLevel(logging.INFO)
    # setLevel идемпотентен: повторный вызов configure_logging не меняет уровень.
    for name in HTTP_CLIENT_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
    for name in UVICORN_LOGGERS:
        # Свои хендлеры uvicorn (plain-текст) снимаем: записи уходят
        # propagate-ом на root, в stdout — только JSON.
        uvicorn = logging.getLogger(name)
        uvicorn.handlers.clear()
        uvicorn.propagate = True
    logging.getLogger("uvicorn.error").addFilter(UVICORN_DUPLICATE_FILTER)


def _log_unhandled_exception(
    message: str,
    exc_type: type[BaseException],
    exc_value: BaseException | None,
    exc_traceback: TracebackType | None,
) -> None:
    """Необработанное исключение — одна JSON-запись со стектрейсом.

    Интерпретатор передаёт в хук готовый экземпляр исключения; типы хука
    допускают `None` — тогда в записи остаётся имя типа в тексте.
    """
    if exc_value is None:
        logger.error("%s: %s", message, exc_type.__qualname__)
        return
    logger.error(message, exc_info=(exc_type, exc_value, exc_traceback))


def _main_thread_excepthook(
    exc_type: type[BaseException],
    exc_value: BaseException | None,
    exc_traceback: TracebackType | None,
) -> None:
    """Необработанное исключение главного потока — JSON-запись со стектрейсом."""
    _log_unhandled_exception("необработанное исключение", exc_type, exc_value, exc_traceback)


def _threading_excepthook(args: threading.ExceptHookArgs) -> None:
    """Необработанное исключение стороннего потока — та же JSON-запись."""
    thread_name = args.thread.name if args.thread is not None else "без объекта потока"
    _log_unhandled_exception(
        "необработанное исключение в потоке " + thread_name,
        args.exc_type,
        args.exc_value,
        args.exc_traceback,
    )


def install_excepthooks() -> None:
    """Необработанное исключение любого потока пишет логгер, не интерпретатор.

    Дефолтные хуки печатают текстовый дамп в stderr — не-JSON строку дропает
    коллектор, и падение процесса не оставило бы следа в Loki. Хук ставит
    любой вход — `create_app` API и `run_worker` воркера, — сразу после
    настройки логов: ошибка старта (`ValidationError` pydantic из кривого
    окружения) выходит из процесса JSON-записью с `exception.type` и
    стектрейсом, и crash-loop виден в Loki (замечание ревью PR #130).
    """
    sys.excepthook = _main_thread_excepthook
    threading.excepthook = _threading_excepthook
