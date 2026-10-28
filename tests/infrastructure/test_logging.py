"""Логи: JSON-поля записи, trace-контекст, дубль uvicorn и запретный список.

Хендлер здесь — та же связка, что ставит `configure_logging` (производственные
`JsonFormatter` и `TraceContextFilter`), но пишущая в `StringIO`: проверяется
форма записи, а не конкретный поток. Спаны пишет в память провайдер из
`tests/conftest.py` (фикстура `spans`).
"""

import contextlib
import io
import json
import logging
import re
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from uuid import UUID

import httpx
import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import SecretStr

from app.api import log_redaction
from app.application.ports.llm_gateway import LlmGateway
from app.application.use_cases.build_review_context import (
    build_review_context,
    fetch_review_diff,
)
from app.domain.llm_errors import LlmError
from app.infrastructure.llm.openai_compatible import OpenAiCompatibleGateway
from app.infrastructure.logging import (
    JsonFormatter,
    ReviewRunIdFilter,
    TraceContextFilter,
    UvicornDuplicateFilter,
    configure_logging,
    record_to_fields,
    review_run_id_scope,
)
from app.infrastructure.queue.rabbitmq import to_wire_message
from app.infrastructure.vcs.github import GitHubVcsGateway
from app.infrastructure.vcs.github_auth import GitHubAppAuth
from app.worker.factory import build_review_handler

from ..fakes.context import FakeCache, FakeExtractor
from ..fakes.unit_of_work import FakeUnitOfWork
from ..fakes.vcs_gateway import RecordingVcsGateway
from ..worker.test_review_handler import (
    JOB,
    LIMITS,
    LLM_LIMITS,
    TEXTS,
    _Deps,
    _seeded_uow,
)
from .conftest import RsaKeyPair

# --- общий харнесс: производственный JSON-хендлер, пишущий в память ---------------

SELFTEST_LOGGER = logging.getLogger("observability.selftest")


@dataclass
class JsonLog:
    """Собранный вывод JSON-хендлера: строки и разобранные записи."""

    out: io.StringIO

    def lines(self) -> list[str]:
        return [line for line in self.out.getvalue().splitlines() if line]

    def fields(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.lines()]

    def text(self) -> str:
        return self.out.getvalue()


def _json_handler(out: io.StringIO) -> logging.Handler:
    """Та же связка, что ставит `configure_logging`: JSON + оба фильтра."""
    handler = logging.StreamHandler(out)
    handler.setFormatter(JsonFormatter("dmc-268-test"))
    handler.addFilter(TraceContextFilter())
    handler.addFilter(ReviewRunIdFilter())
    return handler


@contextlib.contextmanager
def json_log(logger: logging.Logger) -> Iterator[JsonLog]:
    """Записи `logger` через производственный JSON-хендлер, в память."""
    out = io.StringIO()
    handler = _json_handler(out)
    level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    try:
        yield JsonLog(out)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


@pytest.fixture
def selftest() -> Iterator[JsonLog]:
    """Отдельный логгер: записи теста не смешиваются с посторонними."""
    with json_log(logging.getLogger("observability.selftest")) as log:
        yield log


@pytest.fixture
def root_log() -> Iterator[JsonLog]:
    """Все записи, дошедшие до root, — так пишут модули приложения."""
    with json_log(logging.getLogger()) as log:
        yield log


# --- 1.1: поля записи ------------------------------------------------------------


def test_record_to_fields_follows_the_otel_log_record_model() -> None:
    record = logging.LogRecord(
        name="app.selftest",
        level=logging.ERROR,
        pathname=__file__,
        lineno=17,
        msg="этап %s завершён",
        args=("анализ",),
        exc_info=None,
        func=None,
        sinfo=None,
    )
    record.created = 1762855200.5

    assert record_to_fields(record, "dmc-268-api") == {
        "timestamp": "2025-11-11T10:00:00.500000Z",
        "severity": "ERROR",
        "message": "этап анализ завершён",
        "logger": "app.selftest",
        "service.name": "dmc-268-api",
    }


def test_a_record_inside_a_span_carries_its_context_and_extra(
    selftest: JsonLog, spans: InMemorySpanExporter
) -> None:
    with trace.get_tracer(__name__).start_as_current_span("review.analysing") as span:
        SELFTEST_LOGGER.info("этап идёт", extra={"review_run_id": str(JOB.review_run_id)})

    [fields] = selftest.fields()
    context = span.get_span_context()
    assert context is not None
    assert fields["trace_id"] == trace.format_trace_id(context.trace_id)
    assert re.fullmatch(r"[0-9a-f]{32}", fields["trace_id"])
    assert fields["span_id"] == trace.format_span_id(context.span_id)
    # Два hex-символа, как в traceparent; бит сэмплирования установлен
    # (SDK может добавить и случайный бит — формат, а не значение «01»).
    assert re.fullmatch(r"[0-9a-f]{2}", fields["trace_flags"])
    assert int(fields["trace_flags"], 16) & 0b01 == 0b01
    assert fields["review_run_id"] == str(JOB.review_run_id)


def test_a_record_outside_a_span_is_kept_without_the_trace_fields(
    selftest: JsonLog,
) -> None:
    SELFTEST_LOGGER.warning("вне спана")

    [fields] = selftest.fields()
    assert fields["message"] == "вне спана"
    assert {"trace_id", "span_id", "trace_flags"}.isdisjoint(fields)


def test_outside_a_span_an_extra_trace_field_reaches_the_json(
    selftest: JsonLog,
) -> None:
    """Вне спана коллизия extra с trace-полем не теряется молча: фильтр поле
    не ставит, и значение из extra доходит до JSON (замечание ревью PR #130)."""
    SELFTEST_LOGGER.warning("вне спана", extra={"trace_id": "из-extra"})

    [fields] = selftest.fields()
    assert fields["trace_id"] == "из-extra"


def test_inside_a_span_the_real_context_wins_over_extra(
    selftest: JsonLog, spans: InMemorySpanExporter
) -> None:
    """Фильтры выполняются после помещения extra в запись и перезаписывают
    коллизию настоящим контекстом: подделать trace_id через extra нельзя."""
    with trace.get_tracer(__name__).start_as_current_span("review.analysing"):
        SELFTEST_LOGGER.info("этап идёт", extra={"trace_id": "подделка"})

    [fields] = selftest.fields()
    context = spans.get_finished_spans()[0].get_span_context()
    assert context is not None
    assert fields["trace_id"] == trace.format_trace_id(context.trace_id)
    assert fields["trace_id"] != "подделка"


def test_the_run_id_attribute_lives_on_the_record_for_the_otlp_transport() -> None:
    """`review_run_id` — атрибут записи: его читает не только JSON-форматтер,
    но и OTLP-экспортёр SDK (`LoggingHandler` кладёт `vars(record)` в атрибуты
    лога), поэтому оба транспорта несут одни поля (решение #89)."""
    record = SELFTEST_LOGGER.makeRecord(
        SELFTEST_LOGGER.name, logging.WARNING, __file__, 1, "этап прогона", (), None
    )
    with review_run_id_scope(str(JOB.review_run_id)):
        ReviewRunIdFilter().filter(record)

    assert record.__dict__["review_run_id"] == str(JOB.review_run_id)


def test_the_record_carries_its_logger_name(selftest: JsonLog) -> None:
    """Поле `logger` — имя логгера записи: в Loki отличимы `uvicorn.access`
    от `app.worker.factory` (имя stdlib кладёт в `record.name`, в JSON оно
    раньше не попадало)."""
    SELFTEST_LOGGER.warning("источник записи")

    [fields] = selftest.fields()
    assert fields["logger"] == "observability.selftest"


# --- 1.1.1: контекст прогона — атрибутом записи, не вклейкой ------------------------


def test_the_run_id_scope_becomes_the_record_attribute(selftest: JsonLog) -> None:
    """Внутри `review_run_id_scope` запись несёт `review_run_id` — без extra и без
    вклейки в текст: контекст ставит фильтр хендлера, как в обработке сообщения."""
    with review_run_id_scope(str(JOB.review_run_id)):
        SELFTEST_LOGGER.warning("этап прогона завершился")

    [fields] = selftest.fields()
    assert fields["review_run_id"] == str(JOB.review_run_id)
    assert fields["message"] == "этап прогона завершился"
    assert str(JOB.review_run_id) not in fields["message"]


def test_outside_the_scope_the_record_has_no_run_id_attribute(
    selftest: JsonLog,
) -> None:
    """Вне контекста поля нет, запись не теряется; после выхода контекст не течёт."""
    with review_run_id_scope(str(JOB.review_run_id)):
        pass

    SELFTEST_LOGGER.warning("вне обработки сообщения")

    [fields] = selftest.fields()
    assert fields["message"] == "вне обработки сообщения"
    assert "review_run_id" not in fields


def test_a_nested_scope_is_restored_by_the_outer_one(selftest: JsonLog) -> None:
    """Вложенный контекст: выход из внутреннего возвращает внешний, а не стирает его."""
    with review_run_id_scope("внешний-прогон"):
        with review_run_id_scope("внутренний-прогон"):
            SELFTEST_LOGGER.warning("внутри")
        SELFTEST_LOGGER.warning("после внутреннего")

    inner, outer = selftest.fields()
    assert inner["review_run_id"] == "внутренний-прогон"
    assert outer["review_run_id"] == "внешний-прогон"


def test_exc_info_becomes_the_stacktrace_of_the_same_record(selftest: JsonLog) -> None:
    try:
        raise ValueError("сбой этапа")
    except ValueError as error:
        SELFTEST_LOGGER.error("упало", exc_info=error)

    (line,) = selftest.lines()
    assert "\n" not in line  # одна запись — одна строка JSON
    [fields] = selftest.fields()
    assert fields["severity"] == "ERROR"
    assert "ValueError: сбой этапа" in fields["exception.stacktrace"]


def test_exc_info_leaves_the_exception_type_of_the_record(selftest: JsonLog) -> None:
    """`exception.type` — qualname класса исключения: редакция коллектора
    гасит `exception.message`, `exception.stacktrace` и тело записи, а
    диагноз остаётся (в трейсах тип сохраняется по той же спеке — паритет)."""

    class StageError(ValueError):
        """Локальный класс: qualname ≠ name, проверяется именно qualname."""

    try:
        raise StageError("сбой этапа")
    except StageError as error:
        SELFTEST_LOGGER.error("упало", exc_info=error)

    [fields] = selftest.fields()
    assert fields["exception.type"].endswith("StageError")


def test_extra_cannot_override_the_service_fields(selftest: JsonLog) -> None:
    """`extra` не перетирает служебные поля: подделка `severity` не меняет
    уровень записи, `timestamp`/`service.name`/`logger` остаются нашими."""
    SELFTEST_LOGGER.error(
        "упало",
        extra={
            "severity": "INFO",
            "timestamp": "2026-01-01T00:00:00Z",
            "service.name": "чужой-сервис",
            "logger": "fake",
        },
    )

    [fields] = selftest.fields()
    assert fields["severity"] == "ERROR"
    assert fields["service.name"] == "dmc-268-test"
    assert fields["logger"] == "observability.selftest"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", fields["timestamp"])


# --- 1.2: дубль uvicorn и направление uvicorn.* на JSON-хендлер -------------------


def _uvicorn_record(message: str) -> logging.LogRecord:
    return logging.LogRecord(
        "uvicorn.error", logging.ERROR, __file__, 1, message, None, None, None, None
    )


def test_the_uvicorn_duplicate_is_dropped_and_other_records_pass() -> None:
    """Сообщение с завершающим переводом строки — как его пишет uvicorn."""
    fltr = UvicornDuplicateFilter()

    assert fltr.filter(_uvicorn_record("Exception in ASGI application\n")) is False
    assert fltr.filter(_uvicorn_record("Application startup complete.")) is True


def test_configure_logging_routes_uvicorn_loggers_to_the_single_json_handler() -> None:
    configure_logging("dmc-268-api")
    root = logging.getLogger()
    ours = [
        handler
        for handler in root.handlers
        if isinstance(handler.formatter, JsonFormatter)
    ]
    assert len(ours) == 1
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn = logging.getLogger(name)
        assert uvicorn.handlers == []
        assert uvicorn.propagate is True
    installed = logging.getLogger("uvicorn.error").filters
    assert any(isinstance(f, UvicornDuplicateFilter) for f in installed)

    configure_logging("dmc-268-worker")  # повторный вызов не плодит хендлеры
    again = [
        handler
        for handler in root.handlers
        if isinstance(handler.formatter, JsonFormatter)
    ]
    assert len(again) == 1


def test_configure_logging_writes_to_the_given_stream() -> None:
    """Миграции передают `sys.stderr`: stdout alembic CLI читают скрипты деплоя (#232)."""
    configure_logging("dmc-268-api")
    [handler] = [h for h in logging.getLogger().handlers if isinstance(h.formatter, JsonFormatter)]
    assert isinstance(handler, logging.StreamHandler)
    previous = handler.stream
    stream = io.StringIO()
    try:
        configure_logging("dmc-268-migrate", stream=stream)
        logging.getLogger("alembic.runtime.migration").info("Context impl PostgresqlImpl.")
        record = json.loads(stream.getvalue())
        assert record["service.name"] == "dmc-268-migrate"
        assert record["message"] == "Context impl PostgresqlImpl."

        # Вызов без `stream` возвращает stdout: миграции in-process в тестах
        # не уводят логи API в чужой поток.
        configure_logging("dmc-268-api")
        assert handler.stream is sys.stdout
    finally:
        handler.setStream(previous)


def test_uvicorn_error_records_arrive_as_json_except_the_duplicate() -> None:
    configure_logging("dmc-268-api")
    uvicorn = logging.getLogger("uvicorn.error")

    with json_log(uvicorn) as log:
        uvicorn.error("Application startup complete.")
        uvicorn.error("Exception in ASGI application\n", exc_info=ValueError("сбой"))

    [fields] = log.fields()
    assert fields["message"] == "Application startup complete."


# --- 1.3: httpx/httpcore — молчание на INFO, URL запроса не в логе ----------------


class _OkHandler(BaseHTTPRequestHandler):
    """Локальный HTTP-сервер: 200 на любой GET, свой access-лог не пишет."""

    def do_GET(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.end_headers()
        self.wfile.write(b"ok\n")

    def log_message(self, format: str, *args: Any) -> None:
        pass  # собственный access-лог сервера в тесте не нужен


def test_configure_logging_puts_httpx_and_httpcore_on_warning() -> None:
    """httpx на INFO пишет «HTTP Request: <метод> <URL>» о каждом исходящем
    запросе: имя приватного репозитория, пути файлов и токены query уходили
    бы в лог целиком; WARNING оставляет клиентам только настоящие сбои."""
    configure_logging("dmc-268-api")

    for name in ("httpx", "httpcore"):
        assert logging.getLogger(name).level == logging.WARNING


def test_an_outgoing_httpx_request_url_stays_out_of_the_log(root_log: JsonLog) -> None:
    """Настоящий запрос httpx (не мок): URL — ни в одной записи root.

    Строка «HTTP Request: …» пишется только после ответа, поэтому запрос
    идёт к поднятому в тесте локальному серверу: без перевода логгера на
    WARNING URL с путём и токеном query попадал в лог открытым текстом.
    Недоступный адрес проверяется там же — попытка соединения URL не пишет.
    """
    configure_logging("dmc-268-api")

    server = HTTPServer(("127.0.0.1", 0), _OkHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = (
            f"http://127.0.0.1:{server.server_address[1]}"
            "/private-repo/contents/app/main.py?token=ghs_url-token"
        )
        # trust_env=False: прокси из окружения изменил бы адрес назначения.
        with httpx.Client(timeout=2, trust_env=False) as client:
            assert client.get(url).status_code == HTTPStatus.OK
            # Недоступный локальный адрес: попытка соединения URL не пишет.
            with pytest.raises(httpx.ConnectError):
                client.get("http://127.0.0.1:1/private-repo?token=ghs_url-token")
            # Клиенты не заглушены целиком: WARNING и выше в лог попадают.
            logging.getLogger("httpx").warning("сбой исходящего запроса")
    finally:
        server.shutdown()
        server.server_close()

    text = root_log.text()
    assert "HTTP Request:" not in text
    assert "/private-repo" not in text
    assert "ghs_url-token" not in text
    assert "сбой исходящего запроса" in text


# --- 1.4: запретный список — секреты не попадают в запись -------------------------

# Маркеры: если любой из них окажется в записи лога — запрет нарушен.
DIFF_SECRET = "SECRET-DIFF-CODE-42"
MODEL_KEY_1 = "sk-test-model-key-1"
MODEL_KEY_2 = "sk-test-model-key-2"

SECRET_DIFF = (
    "diff --git a/x.py b/x.py\n"
    "--- a/x.py\n+++ b/x.py\n"
    "@@ -1,2 +1,2 @@\n"
    "-old\n"
    f"+{DIFF_SECRET} print('привет')\n"
)


class _DepsWithDiff(_Deps):
    """Обработчик воркера с диффом-маркером и настоящим адаптером модели."""

    def __init__(self, uow: FakeUnitOfWork, gateway: LlmGateway) -> None:
        super().__init__(uow, gateway)
        self._diff_gateway = RecordingVcsGateway(diff=SECRET_DIFF)

    def vcs_gateway(self) -> RecordingVcsGateway:
        return self._diff_gateway


def _provider_quoting_keys(request: httpx.Request) -> httpx.Response:
    """Провайдер из аудита #89: цитирует ключ в тексте ошибки."""
    authorization = request.headers["Authorization"]
    if authorization.endswith(MODEL_KEY_1[-1]):
        return httpx.Response(
            401, json={"error": {"message": f"неверный ключ {MODEL_KEY_1}"}}
        )
    return httpx.Response(
        500, json={"error": {"message": f"сбой провайдера, ключ {MODEL_KEY_2}"}}
    )


def test_a_failing_model_call_leaves_no_keys_or_request_bodies_in_the_log(
    root_log: JsonLog,
) -> None:
    """Вызов модели через настоящий обработчик воркера: предупреждения о ключах
    и запись об ошибке не несут ни ключей, ни тела запроса (в нём код из диффа)."""
    client = httpx.Client(
        base_url="https://provider.test/v1",
        transport=httpx.MockTransport(_provider_quoting_keys),
    )
    gateway = OpenAiCompatibleGateway(
        client,
        "test-model",
        [SecretStr(MODEL_KEY_1), SecretStr(MODEL_KEY_2)],
        name="provider",
        max_attempts=1,
    )
    handler = build_review_handler(
        _DepsWithDiff(_seeded_uow(), gateway), LIMITS, LLM_LIMITS, TEXTS
    )

    with pytest.raises(LlmError):
        handler(json.dumps(to_wire_message(JOB)).encode("utf-8"))

    fields = root_log.fields()
    assert any(f["severity"] == "ERROR" for f in fields)  # ошибка задачи записана
    assert any(f["severity"] == "WARNING" for f in fields)  # и ротация ключей
    text = root_log.text()
    assert MODEL_KEY_1 not in text
    assert MODEL_KEY_2 not in text
    assert DIFF_SECRET not in text


APP_ID = "123456"
INSTALLATION_TOKEN = "ghs_test-installation-token"
FAR_FUTURE = "2099-01-01T00:00:00Z"


def _github_handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.startswith("/app/installations/"):
        return httpx.Response(200, json={"token": INSTALLATION_TOKEN, "expires_at": FAR_FUTURE})
    if "/compare/" in path:
        return httpx.Response(200, text=SECRET_DIFF)
    if "/contents/" in path:
        # Файла на коммите нет — допустимое отсутствие, а не сбой.
        return httpx.Response(404, json={"message": "Not Found"})
    # Метаданные и коммиты PR: 5xx до исчерпания попыток — реальный сбой GitHub.
    return httpx.Response(500)


def test_a_github_failure_leaves_no_diff_or_token_in_the_log(
    root_log: JsonLog, rsa_key_pair: RsaKeyPair
) -> None:
    """Ответ GitHub прошёл через адаптер: дифф получен, но в предупреждения
    сборки контекста (они со стектрейсами) не попадают ни код из диффа, ни
    installation token."""
    sleeps: list[float] = []
    client = httpx.Client(
        base_url="https://api.github.com", transport=httpx.MockTransport(_github_handler)
    )
    auth = GitHubAppAuth(APP_ID, rsa_key_pair.private_pem, client)
    gateway = GitHubVcsGateway(auth, client, sleep=sleeps.append)

    diff = fetch_review_diff(JOB, gateway, max_bytes=LIMITS.diff_max_bytes)
    assert DIFF_SECRET in diff  # секретный код действительно пришёл

    build_review_context(
        JOB,
        diff,
        vcs=gateway,
        extractor=FakeExtractor(),
        cache=FakeCache(),
        limits=LIMITS,
        review_run_id=JOB.review_run_id,
        now=datetime(2026, 10, 22, tzinfo=UTC),
        new_id=lambda: UUID(int=7),
    )

    fields = root_log.fields()
    warnings = [
        f["message"] for f in fields if f["severity"] == "WARNING" and "недоступны" in f["message"]
    ]
    assert warnings == [
        "метаданные PR 42 недоступны для контекста",
        "коммиты PR 42 недоступны для контекста",
    ]
    with_stacktrace = [f for f in fields if "exception.stacktrace" in f]
    assert with_stacktrace  # стектрейсы есть — и секретов в них нет
    assert any(
        "VcsUnavailableError" in f["exception.stacktrace"] for f in with_stacktrace
    )
    text = root_log.text()
    assert INSTALLATION_TOKEN not in text
    assert DIFF_SECRET not in text


def test_a_failing_task_log_carries_the_run_id_as_an_attribute(
    root_log: JsonLog,
) -> None:
    """Настоящий обработчик воркера: упавшая задача пишет `job.id` в тексте, а
    `review_run_id` — атрибутом из контекста (фильтр хендлера), не вклейкой."""
    body = json.dumps(to_wire_message(JOB)).encode("utf-8")
    handler = build_review_handler(_Deps(FakeUnitOfWork()), LIMITS, LLM_LIMITS, TEXTS)

    with pytest.raises(LookupError):
        handler(body)

    [fields] = [f for f in root_log.fields() if f["severity"] == "ERROR"]
    assert fields["review_run_id"] == str(JOB.review_run_id)
    assert str(JOB.id) in fields["message"]
    assert str(JOB.review_run_id) not in fields["message"]


def test_the_auth_callback_query_stays_out_of_the_access_log() -> None:
    """Access-лог uvicorn: query скрыт фильтром `AccessQueryRedaction` до
    JSON-форматтера — на путях входа в нём `code` и `state`."""
    log_redaction.install()
    uvicorn = logging.getLogger("uvicorn.access")

    with json_log(uvicorn) as log:
        # Формат uvicorn: '%s - "%s %s HTTP/%s" %d' — адрес, метод, путь, версия, статус.
        uvicorn.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:50000",
            "GET",
            "/api/v1/auth/github/callback?code=code-secret-1&state=state-secret-1",
            "1.1",
            302,
        )

    [fields] = log.fields()
    assert "code-secret-1" not in fields["message"]
    assert "state-secret-1" not in fields["message"]
    assert "/api/v1/auth/github/callback?<скрыто>" in fields["message"]


def test_the_query_is_redacted_on_any_path_of_the_access_log() -> None:
    """Access-лог uvicorn: query скрыт на любом пути, не только на входе —
    поисковой query `GET /reviews?q=…` кабинета в логе тоже нет."""
    log_redaction.install()
    uvicorn = logging.getLogger("uvicorn.access")

    with json_log(uvicorn) as log:
        uvicorn.info(
            '%s - "%s %s HTTP/%s" %d',
            "127.0.0.1:50000",
            "GET",
            "/api/v1/reviews?q=поиск-по-замечаниям-пользователя",
            "1.1",
            200,
        )

    [fields] = log.fields()
    assert "поиск-по-замечаниям" not in fields["message"]
    assert "/api/v1/reviews?<скрыто>" in fields["message"]
