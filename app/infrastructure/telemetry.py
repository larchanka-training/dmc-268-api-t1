"""Трассировка OpenTelemetry: провайдер, инструментирование и адаптер `StageTracer`.

Телеметрия — для команды: где тормозит, что падает. Пользователю она не показывается,
ни один ответ API из неё не строится; длительность, токены и шаги прогона кабинет берёт
из базы (спека `observability`).

Что включено:

- SDK-провайдер трейсов в API и воркере (`configure_tracing`). Он нужен, даже когда
  спаны никуда не уходят: без него у спанов нет настоящего trace id, а `X-Trace-ID` — это
  trace id спана запроса;
- HTTP-сервер — встроенная телеметрия FastAPI (она берёт глобальный провайдер сама,
  отдельный пакет инструментирования не нужен); httpx (вызовы GitHub и модели) и
  SQLAlchemy — пакеты `opentelemetry-instrumentation-*`;
- спаны этапов прогона — `OtelStageTracer`, адаптер порта, который зовёт `StepRecorder`;
- спан периодического прохода воркера — `sweep_span`.

Экспорт не настраиваем: по умолчанию спаны не уходят никуда и ошибок экспорта в логе
нет. `OTEL_TRACES_EXPORTER=console` печатает их в stdout — для отладки. Остальные
значения, в том числе `otlp`, пока не поддержаны: экспорт в хранилище трейсов — задача
#89. Автонастройку FastAPI (`FASTAPI_OTEL_AUTO_CONFIGURE`) не включаем.

Query серверного спана на путях входа скрывается (`redact_query`, `QueryRedaction`): в
нём `code` и `state` callback'а OAuth.

Глобальный пропагатор пустой: входящему `traceparent` от HTTP-клиента не доверяем
(запрос всегда начинает новый трейс), а httpx не отдаёт наш trace id GitHub и
провайдерам модели. Контекст между API и воркером идёт только через заголовки AMQP
нашей очереди — пропагатором W3C в `app/infrastructure/queue/rabbitmq.py`.
"""

import contextlib
import logging
import os
from collections.abc import Iterator
from contextvars import Token
from uuid import UUID
from weakref import WeakKeyDictionary

from opentelemetry import context as otel_context
from opentelemetry import metrics, propagate, trace
from opentelemetry.context import Context
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Span, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.trace import Status, StatusCode
from sqlalchemy import Engine

from app.domain.enums import ReviewRunStage

logger = logging.getLogger(__name__)

API_SERVICE = "dmc-268-api"
WORKER_SERVICE = "dmc-268-worker"

# Имя атрибута — как у поля в API и в логах: по нему трейс ищется от прогона.
REVIEW_RUN_ID = "review_run_id"

_tracer = trace.get_tracer(__name__)

# Провайдер, который поставил `configure_tracing`: его и закрывает `shutdown_tracing`.
# Чужой провайдер (тест со своим экспортёром) не закрываем.
_installed: TracerProvider | None = None


class _NoPropagation(TextMapPropagator):
    """Пропагатор, который ничего не берёт из заголовков и ничего в них не кладёт.

    Пустой `CompositePropagator` не годится: его `extract` без контекста отдаёт
    `None`, а FastAPI ждёт контекст. Здесь — пустой контекст, то есть новый трейс.
    """

    def extract(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        getter: Getter[CarrierT] = default_getter,
    ) -> Context:
        return context if context is not None else Context()

    def inject(
        self,
        carrier: CarrierT,
        context: Context | None = None,
        setter: Setter[CarrierT] = default_setter,
    ) -> None:
        return None

    @property
    def fields(self) -> set[str]:
        return set()


def configure_tracing(service_name: str) -> None:
    """Поставить пустой пропагатор, SDK-провайдер трейсов и инструментирование.

    Провайдер глобальный и ставится один раз на процесс; если он уже стоит (второй
    вызов или тест со своим `InMemorySpanExporter`), его не трогаем. `OTEL_SERVICE_NAME`
    переопределяет `service_name`.
    """
    global _installed
    # Пропагатор ставится всегда, и поверх `OTEL_PROPAGATORS`: доверие к входящим
    # заголовкам — правило сервиса, а не настройка окружения.
    propagate.set_global_textmap(_NoPropagation())
    instrument_httpx()
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = build_tracer_provider(service_name)
        trace.set_tracer_provider(provider)
        _installed = provider


def shutdown_tracing() -> None:
    """Выгрузить накопленные спаны и закрыть провайдер, который поставил `configure_tracing`.

    Зовётся при остановке: в lifespan API и при выходе воркера. `atexit` SDK закрыл бы
    провайдер и сам, но только при обычном выходе интерпретатора — смерть от SIGTERM
    с обработчиком по умолчанию его не вызывает, и спаны батч-процессора пропадают.
    """
    if _installed is not None:
        _installed.shutdown()


def build_tracer_provider(service_name: str) -> TracerProvider:
    """SDK-провайдер с экспортом из `OTEL_TRACES_EXPORTER`: `console` или никакого."""
    attributes = (
        {} if os.environ.get("OTEL_SERVICE_NAME") else {SERVICE_NAME: service_name}
    )
    provider = TracerProvider(resource=Resource.create(attributes))
    exporter = os.environ.get("OTEL_TRACES_EXPORTER", "none").strip().lower()
    if exporter == "console":
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    elif exporter not in ("", "none"):
        logger.warning(
            "OTEL_TRACES_EXPORTER=%s не поддержан: спаны не экспортируются (поддержан console)",
            exporter,
        )
    return provider


def instrument_httpx() -> None:
    """Спаны на исходящие запросы httpx: GitHub и провайдеры модели."""
    instrumentor = HTTPXClientInstrumentor()
    if not instrumentor.is_instrumented_by_opentelemetry:
        instrumentor.instrument()


def instrument_engine(engine: Engine) -> None:
    """Спаны на запросы к базе через `engine`.

    Слушатели событий вешаются на этот движок напрямую (`EngineTracer`), а не через
    `SQLAlchemyInstrumentor().instrument()`: тот объявляет `sqlalchemy < 2.1` и на нашей
    2.1 молча ничего не делает, а с пропуском проверки ещё и подменяет
    `create_async_engine`, которому нужен `greenlet`. События движка
    (`before_cursor_execute` и т. п.) в 2.1 те же — спаны запросов проверяет сквозной
    тест трейса. Счётчик соединений — no-op: метрик в сервисе нет.

    `EngineTracer` — внутренний класс пакета, без обещаний совместимости: пакет держит
    верхняя граница в `pyproject.toml`, а если класс всё же переехал или сменил
    сигнатуру, сервис стартует без спанов SQL с одним предупреждением, а не падает.
    """
    try:
        # Импорт здесь, а не наверху модуля: переезд класса — тоже `ImportError` этого вызова.
        from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer

        tracer = trace.get_tracer("opentelemetry.instrumentation.sqlalchemy")
        connections = metrics.get_meter(__name__).create_up_down_counter(
            "db.client.connections.usage", unit="connections"
        )
        EngineTracer(tracer, engine, connections)  # type: ignore[no-untyped-call]  # пакет без аннотаций
    except (ImportError, TypeError, AttributeError):
        logger.warning(
            "спаны запросов к базе выключены: EngineTracer несовместим", exc_info=True
        )


# Атрибуты серверного спана FastAPI (semconv HTTP).
_URL_PATH = "url.path"
_URL_QUERY = "url.query"


class QueryRedaction(SpanProcessor):
    """Заменяет `url.query` спана на `marker`, если `url.path` начинается с `path_prefix`.

    Встроенная телеметрия FastAPI кладёт query в атрибуты сразу, при создании спана
    (`start_span(attributes=...)`), и скрывает только свой короткий список параметров —
    `code` и `state` callback'а входа в него не входят. Штатного хука на атрибуты у
    `FastAPI(telemetry=...)` нет, а `exclude` выключил бы спан запроса целиком, и с ним
    `X-Trace-ID`. `on_start` SDK вызывает сразу после создания спана, пока тот
    изменяем и задолго до экспорта (`on_end`): исходное значение не уходит никуда.
    """

    def __init__(self, path_prefix: str, marker: str) -> None:
        self._path_prefix = path_prefix
        self._marker = marker

    def on_start(self, span: Span, parent_context: Context | None = None) -> None:
        attributes = span.attributes or {}
        path = attributes.get(_URL_PATH)
        if (
            _URL_QUERY in attributes
            and isinstance(path, str)
            and path.startswith(self._path_prefix)
        ):
            span.set_attribute(_URL_QUERY, self._marker)


# Какие скрытия уже стоят на провайдере: повторный `create_app` (в тестах) не плодит
# процессоры.
_redactions: WeakKeyDictionary[TracerProvider, set[tuple[str, str]]] = WeakKeyDictionary()


def redact_query(path_prefix: str, marker: str) -> None:
    """Скрывать query серверных спанов на путях `path_prefix` — `QueryRedaction`.

    Ставится на глобальный SDK-провайдер, поэтому зовётся после `configure_tracing`.
    """
    provider = trace.get_tracer_provider()
    if not isinstance(provider, TracerProvider):
        return
    installed = _redactions.setdefault(provider, set())
    if (path_prefix, marker) not in installed:
        provider.add_span_processor(QueryRedaction(path_prefix, marker))
        installed.add((path_prefix, marker))


@contextlib.contextmanager
def sweep_span() -> Iterator[None]:
    """Спан периодического прохода воркера (зависшие прогоны, истёкшие сессии).

    Без него каждый запрос к базе прохода — отдельный корневой трейс. Исключение
    прохода спан записывает и закрывается со статусом ошибки.
    """
    with _tracer.start_as_current_span("review.sweep"):
        yield


def current_trace_id() -> str | None:
    """Trace id текущего спана — 32 строчных hex; `None`, если спана нет."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return trace.format_trace_id(span_context.trace_id)


class _OtelStageSpan:
    def __init__(self, span: trace.Span, token: Token[Context]) -> None:
        self._span = span
        self._token = token

    def end(self, error_code: str | None) -> None:
        if error_code is not None:
            self._span.set_attribute("error.type", error_code)
            self._span.set_status(Status(StatusCode.ERROR, error_code))
        self._span.end()
        otel_context.detach(self._token)


class OtelStageTracer:
    """Адаптер `StageTracer`: спан `review.<этап>` с атрибутом `review_run_id`.

    Спан этапа делается текущим, пока этап идёт: запросы httpx и SQLAlchemy этапа
    ложатся в трейс его детьми. Шаги идут строго по одному, поэтому снятие контекста
    в `end` не нарушает порядок.
    """

    def start(self, review_run_id: UUID, stage: ReviewRunStage) -> _OtelStageSpan:
        span = _tracer.start_span(
            f"review.{stage.value}",
            attributes={REVIEW_RUN_ID: str(review_run_id), "review.stage": stage.value},
        )
        token = otel_context.attach(trace.set_span_in_context(span))
        return _OtelStageSpan(span, token)
