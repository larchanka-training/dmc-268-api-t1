"""Трассировка OpenTelemetry: провайдер, инструментирование и адаптер `StageTracer`.

Телеметрия — для команды: где тормозит, что падает. Пользователю она не показывается,
ни один ответ API из неё не строится; длительность, токены и шаги прогона кабинет берёт
из базы (спека `observability`).

Что включено:

- SDK-провайдеры трейсов и метрик в API и воркере (`configure_telemetry`). Провайдер
  трейсов нужен, даже когда спаны никуда не уходят: без него у спанов нет настоящего
  trace id, а `X-Trace-ID` — это trace id спана запроса;
- HTTP-сервер — встроенная телеметрия FastAPI (она берёт глобальные провайдеры сама,
  отдельный пакет инструментирования не нужен); httpx (вызовы GitHub и модели) и
  SQLAlchemy — пакеты `opentelemetry-instrumentation-*`;
- спаны этапов прогона — `OtelStageTracer`, адаптер порта, который зовёт `StepRecorder`;
- спан периодического прохода воркера — `sweep_span`;
- спан прогона миграций — `migrate_span`, его открывает `alembic/env.py`;
- бизнес-метрики для алертов: исходы прогонов (`record_run_finished` — пишет
  адаптер `OtelRunOutcomeRecorder` через порт `RunOutcomeRecorder`),
  длительность этапов (пишет `OtelStageTracer`) и вызовов модели (пишет
  `MeteredLlmGateway`), токены модели (`record_llm_tokens`);
- метрики пула соединений — снимками состояния (`instrument_pool`).

Экспорт включается окружением, без правки кода: `OTEL_TRACES_EXPORTER` — список
значений через запятую; `console` печатает законченные спаны в stdout, `otlp`
отправляет их OTLP/HTTP — endpoint из `OTEL_EXPORTER_OTLP_ENDPOINT` читает сам
экспортёр (приём делает коллектор телеметрии). Без настройки спаны никуда не
уходят и ошибок экспорта в логе нет; неподдержанное значение выключает только
свой экспортёр и предупреждает при старте. Метрики гейтит так же
`OTEL_METRICS_EXPORTER`: по умолчанию (`none`) они собираются, но никуда не
уходят — SDK без гейта сам ставит `otlp` и стучался бы в localhost:4318.
Автонастройка экспортёров FastAPI и его OTel-лог исключений выключены явно
(`create_app`, `telemetry=`).

Query серверного спана на путях входа скрывается (`redact_query`, `QueryRedaction`): в
нём `code` и `state` callback'а OAuth.

Глобальный пропагатор пустой: входящему `traceparent` от HTTP-клиента не доверяем
(запрос всегда начинает новый трейс), а httpx не отдаёт наш trace id GitHub и
провайдерам модели. Контекст между API и воркером идёт только через заголовки AMQP
нашей очереди — пропагатором W3C в `app/infrastructure/queue/rabbitmq.py`.

Валидный входящий `traceparent` всё же не теряется: серверный спан запроса получает на
него span link (`link_client_trace`). Родителем контекст клиента не становится, поэтому
trace id, `X-Trace-ID` и решение сэмплера от заголовка не зависят.
"""

import contextlib
import logging
import os
import time
from collections.abc import Callable, Iterator
from contextvars import Token
from typing import Any
from uuid import UUID
from weakref import WeakKeyDictionary

from opentelemetry import context as otel_context
from opentelemetry import metrics, propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.utils import suppress_instrumentation
from opentelemetry.propagators.textmap import (
    CarrierT,
    Getter,
    Setter,
    TextMapPropagator,
    default_getter,
    default_setter,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics.view import (
    DropAggregation,
    ExplicitBucketHistogramAggregation,
    View,
)
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import Span, SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ALWAYS_ON, ParentBased, Sampler
from opentelemetry.trace import Status, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import Engine
from sqlalchemy.pool import QueuePool

from app.domain.enums import ReviewRunStage

logger = logging.getLogger(__name__)

API_SERVICE = "dmc-268-api"
WORKER_SERVICE = "dmc-268-worker"
# Отдельное имя процессу миграций: в трейсе деплоя видно, что делал `migrate`,
# а не API или воркер (change `add-observability-stack`, задачи 1.1 и 5.1).
MIGRATE_SERVICE = "dmc-268-migrate"

# Имя атрибута — как у поля в API и в логах: по нему трейс ищется от прогона.
REVIEW_RUN_ID = "review_run_id"

# Метрики пула соединений — снимками состояния, а не событиями: событийный
# счётчик contrib (`db.client.connections.usage`) пропускает события внутри
# `untraced()` (опрос `/events` живёт в подавлении инструментирования) и
# дрейфует без шанса выправиться. Имена атрибутов — semconv пула соединений.
CONNECTION_POOL = "app"
_POOL_NAME_ATTRIBUTE = "db.client.connection.pool.name"
_CONNECTION_COUNT = "db.client.connection.count"
_CONNECTION_MAX = "db.client.connection.max"
_STATE_ATTRIBUTE = "db.client.connection.state"

# Бакеты гистограмм длительности, секунды: этапы прогона и вызовы модели
# длятся от десятых доли до минут — на дефолтных миллисекундных бакетах
# почти всё попадало в один бакет, и `histogram_quantile` бесполезен.
DURATION_BUCKET_BOUNDARIES = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300)

# Вид провайдера: конфигурация агрегации по именам приборов. `View` и
# агрегации публичны в установленной SDK (`opentelemetry.sdk.metrics.view`);
# advisory из спецификации (явные границы бакетов) в Python SDK задаётся
# именно агрегацией `ExplicitBucketHistogramAggregation`.
_VIEWS = (
    # Событийный счётчик contrib всё ещё создаётся в `instrument_engine` и
    # получает события (передать None в EngineTracer нельзя — его листенеры
    # зовут `.add`), но не экспортируется: соединения считает снимок пула
    # (`instrument_pool`).
    View(instrument_name="db.client.connections.usage", aggregation=DropAggregation()),
    *(
        View(
            instrument_name=name,
            aggregation=ExplicitBucketHistogramAggregation(
                boundaries=DURATION_BUCKET_BOUNDARIES
            ),
        )
        for name in ("review_stage_duration", "review_llm_call_duration")
    ),
)

_tracer = trace.get_tracer(__name__)

# Метры бизнес-сигналов — из того же глобального провайдера, что и трейсы:
# его ставит `configure_telemetry`, до этого инструменты — прокси без сбора.
_meter = metrics.get_meter(__name__)

# Бизнес-метрики — ручные сигналы для алертов (change `add-observability-stack`):
# исходы прогонов, длительность этапов и вызовов модели, расход токенов.
# Метки — только перечисления (`status`, `stage`, `task`, `model`); данные
# продукта — шаги и токены прогона — остаются в базе, из телеметрии ответы
# не строятся. Бакеты гистограммам задаёт `_VIEWS`.
_review_runs_total = _meter.create_counter(
    "review_runs_total",
    unit="{run}",
    # Прогоны, доведённые до терминального статуса тем, кто этот переход
    # сделал (пайплайн или выметатель), — не обработки сообщений: повторная
    # доставка терминального прогона и сообщение без прогона не пишутся.
    description="Прогоны по терминальному статусу.",
)
_review_stage_duration = _meter.create_histogram(
    "review_stage_duration", unit="s", description="Длительность этапов прогона."
)
_review_llm_call_duration = _meter.create_histogram(
    "review_llm_call_duration", unit="s", description="Длительность вызовов модели."
)
_review_llm_tokens = _meter.create_counter(
    "review_llm_tokens", unit="{token}", description="Токены модели по задачам прогона."
)

# Стабильные имена атрибутов semconv у пакетов contrib: `db.query.text` вместо
# `db.statement`, `http.request.method` и `url.full` вместо `http.method` и `http.url`.
# Пакеты читают `OTEL_SEMCONV_STABILITY_OPT_IN` один раз на процесс, при первом
# инструментировании, — поэтому значение по умолчанию ставится в коде до него, а не
# только в окружении; заданное явно не перетирается. `database`, а не `database/dup`:
# только новые имена, без дубля старых.
SEMCONV_STABILITY_OPT_IN = "database,http"

# Провайдеры, которые поставил `configure_telemetry`: их и закрывает
# `shutdown_tracing`. Чужие провайдеры (тест со своим экспортёром) не закрываем.
_installed: TracerProvider | None = None
_meter_installed: MeterProvider | None = None

# Дефолт лимита длины значений атрибутов спана: без него `url.full` с длинными
# путями и тексты ошибок раздували бы спан и трафик экспорта.
DEFAULT_ATTRIBUTE_VALUE_LENGTH_LIMIT = 4096

# Значение `OTEL_TRACES_EXPORTER` → фабрика экспортёра. Фабрика — функция, а не
# класс: класс ищется в модуле в момент вызова, и тесты подменяют его потоком.
# `otlp` берёт endpoint из `OTEL_EXPORTER_OTLP_ENDPOINT` сам.
_EXPORTER_FACTORIES: dict[str, Callable[[], SpanExporter]] = {
    "console": lambda: ConsoleSpanExporter(),
    "otlp": lambda: OTLPSpanExporter(),
}

# Значение `OTEL_METRICS_EXPORTER` → фабрика экспортёра метрик; тот же список
# через запятую, что у трейсов.
_METRIC_EXPORTER_FACTORIES: dict[str, Callable[[], MetricExporter]] = {
    "console": lambda: ConsoleMetricExporter(),
    "otlp": lambda: OTLPMetricExporter(),
}


def _attribute_value_length_limit() -> int:
    """Лимит длины значений атрибутов: `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` или дефолт.

    Нецелое или отрицательное значение не роняет старт приложения — предупреждение
    и дефолт, как у неподдержанного экспортёра.
    """
    raw = os.environ.get("OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT", "").strip()
    try:
        limit = int(raw) if raw else DEFAULT_ATTRIBUTE_VALUE_LENGTH_LIMIT
        if limit >= 0:
            return limit
    except ValueError:
        pass
    logger.warning(
        "OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT=%s не целое неотрицательное число — лимит %d",
        raw,
        DEFAULT_ATTRIBUTE_VALUE_LENGTH_LIMIT,
    )
    return DEFAULT_ATTRIBUTE_VALUE_LENGTH_LIMIT


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


# Атрибут ссылки на трейс клиента: откуда взят контекст. Своего имени в semconv нет.
LINK_SOURCE = "link.source"
LINK_SOURCE_CLIENT = "client"
_TRACEPARENT = "traceparent"
_W3C = TraceContextTextMapPropagator()


def link_client_trace(traceparent: str | None) -> None:
    """Добавить текущему спану ссылку на контекст клиента из `traceparent`.

    Зовётся на серверном спане запроса (`TraceIdMiddleware`). Ссылка, а не родитель
    (спека `observability`): trace id показывается людям в `X-Trace-ID` и должен быть
    нашим, а флаг `sampled` клиента не должен управлять записью трейсов. Сэмплер решает
    при создании спана, ссылка добавляется после — `parentbased_*` её не видит.

    Разбирает стандартный пропагатор W3C, и только `traceparent`: `tracestate` и baggage
    ему не передаются. Невалидный заголовок — без ссылки и без ошибки. Не записываемый
    спан (сэмплер отказал, трассировка выключена) ссылку молча игнорирует.
    """
    if not traceparent:
        return
    client = trace.get_current_span(
        _W3C.extract({_TRACEPARENT: traceparent})
    ).get_span_context()
    if client.is_valid:
        trace.get_current_span().add_link(client, {LINK_SOURCE: LINK_SOURCE_CLIENT})


def configure_telemetry(service_name: str) -> None:
    """Поставить пустой пропагатор, SDK-провайдеры трейсов и метрик, инструментирование.

    Провайдеры глобальные и ставятся один раз на процесс; если провайдер уже стоит
    (второй вызов или тест со своим `InMemorySpanExporter`/`InMemoryMetricReader`),
    его не трогаем. `OTEL_SERVICE_NAME` переопределяет `service_name`.
    """
    global _installed, _meter_installed
    # Пропагатор ставится всегда, и поверх `OTEL_PROPAGATORS`: доверие к входящим
    # заголовкам — правило сервиса, а не настройка окружения.
    propagate.set_global_textmap(_NoPropagation())
    opt_in_stable_semconv()
    instrument_httpx()
    if not isinstance(trace.get_tracer_provider(), TracerProvider):
        provider = build_tracer_provider(service_name)
        trace.set_tracer_provider(provider)
        _installed = provider
    if not isinstance(metrics.get_meter_provider(), MeterProvider):
        meter_provider = build_meter_provider(service_name)
        metrics.set_meter_provider(meter_provider)
        _meter_installed = meter_provider


def shutdown_tracing() -> None:
    """Выгрузить накопленные спаны и метрики, закрыть провайдеры `configure_telemetry`.

    Зовётся при остановке: в lifespan API и при выходе воркера. `atexit` SDK закрыл бы
    провайдеры и сам, но только при обычном выходе интерпретатора — смерть от SIGTERM
    с обработчиком по умолчанию его не вызывает, и спаны батч-процессора и метрики
    периодического ридера пропадают.
    """
    if _installed is not None:
        _installed.shutdown()
    if _meter_installed is not None:
        _meter_installed.shutdown()


def parse_exporters(value: str) -> list[str]:
    """Разобрать `OTEL_TRACES_EXPORTER`: значения через запятую, без повторов.

    Разбор по спецификации OTel: каждое значение `strip().lower()`; функция чистая и
    валидацию поддержанности не делает — мусор остаётся в списке, а предупреждение
    про него пишет `build_tracer_provider`. Пустое значение — пустой список.
    """
    exporters: list[str] = []
    for item in value.split(","):
        name = item.strip().lower()
        if name and name not in exporters:
            exporters.append(name)
    return exporters


def _service_resource(service_name: str) -> Resource:
    """Resource трейсов и метрик: `service.name` из кода, остальное — из окружения.

    `OTEL_SERVICE_NAME` переопределяет имя; `Resource.create` мержит
    `OTEL_RESOURCE_ATTRIBUTES` сам — `service.version` и
    `deployment.environment.name` задают compose и `infra/`, код не дублирует.
    """
    attributes = (
        {} if os.environ.get("OTEL_SERVICE_NAME") else {SERVICE_NAME: service_name}
    )
    return Resource.create(attributes)


def build_tracer_provider(service_name: str) -> TracerProvider:
    """SDK-провайдер с экспортом из `OTEL_TRACES_EXPORTER` — списком через запятую.

    Процессор ставится на каждый поддержанный экспортёр; неподдержанное значение
    выключает только свой экспортёр и получает одно предупреждение при старте.
    `none` и пустое значение — экспорт выключен, без предупреждений. Сэмплирование
    решает `_sampler`, лимит длины значений атрибутов —
    `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` с дефолтом в коде.
    """
    provider = TracerProvider(
        sampler=_sampler(),
        resource=_service_resource(service_name),
        span_limits=SpanLimits(max_attribute_length=_attribute_value_length_limit()),
    )
    for name in parse_exporters(os.environ.get("OTEL_TRACES_EXPORTER", "")):
        if name == "none":
            continue  # явное «без экспорта» по спецификации OTel
        factory = _EXPORTER_FACTORIES.get(name)
        if factory is None:
            logger.warning(
                "OTEL_TRACES_EXPORTER=%s не поддержан: спаны этому экспортёру не"
                " уходят (поддержаны %s)",
                name,
                ", ".join(sorted(_EXPORTER_FACTORIES)),
            )
            continue
        provider.add_span_processor(BatchSpanProcessor(factory()))
    return provider


def build_meter_provider(service_name: str) -> MeterProvider:
    """SDK-провайдер метрик с экспортом из `OTEL_METRICS_EXPORTER` — как у трейсов.

    Ридер ставится на каждый поддержанный экспортёр; неподдержанное значение
    выключает только свой экспортёр и получает одно предупреждение при старте.
    `none` и пустое значение — экспорт выключен: ридеров нет, метрики никуда
    не уходят и ошибок в логе нет. Гейт обязателен: SDK по умолчанию сам
    ставит `otlp` и без него стучался бы в localhost:4318. Resource — тот же,
    что у трейсов (`_service_resource`); агрегации отдельных приборов задаёт
    `_VIEWS` (дроп старого счётчика contrib, бакеты гистограмм длительности).
    """
    readers = []
    for name in parse_exporters(os.environ.get("OTEL_METRICS_EXPORTER", "")):
        if name == "none":
            continue  # явное «без экспорта» по спецификации OTel
        factory = _METRIC_EXPORTER_FACTORIES.get(name)
        if factory is None:
            logger.warning(
                "OTEL_METRICS_EXPORTER=%s не поддержан: метрики этому экспортёру"
                " не уходят (поддержаны %s)",
                name,
                ", ".join(sorted(_METRIC_EXPORTER_FACTORIES)),
            )
            continue
        readers.append(PeriodicExportingMetricReader(factory()))
    return MeterProvider(
        resource=_service_resource(service_name), metric_readers=readers, views=_VIEWS
    )


def _sampler() -> Sampler | None:
    """Сэмплер из `OTEL_TRACES_SAMPLER`, кроме сэмплера по доле.

    `X-Trace-ID` показывается людям, и по каждому trace id должен находиться трейс: доля
    при старте трейса оставила бы часть кодов из ошибок без трейса. Долю отбирает хвостовое
    сэмплирование в коллекторе. `None` — SDK берёт сэмплер из окружения сам.
    """
    name = os.environ.get("OTEL_TRACES_SAMPLER", "").strip().lower()
    if "traceidratio" not in name:
        return None
    logger.warning(
        "OTEL_TRACES_SAMPLER=%s не применяется: доля прячет трейсы по X-Trace-ID, "
        "пишем parentbased_always_on",
        name,
    )
    return ParentBased(ALWAYS_ON)


def opt_in_stable_semconv() -> None:
    """`OTEL_SEMCONV_STABILITY_OPT_IN` по умолчанию — до первого инструментирования."""
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", SEMCONV_STABILITY_OPT_IN)


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
    тест трейса.

    Счётчик соединений у contrib (`db.client.connections.usage`) — событийный:
    события checkin/checkout внутри `untraced()` (подавление инструментирования,
    живёт на опросе `/events`) им пропускаются, и счётчик дрейфует. Поэтому он
    здесь только создаётся (передать None в `EngineTracer` нельзя — его
    листенеры зовут `.add`), но гасится View-агрегацией в `build_meter_provider`;
    соединения считает снимок пула — `instrument_pool` — и receiver `postgresql`
    коллектора со стороны базы.

    `EngineTracer` — внутренний класс пакета, без обещаний совместимости: пакет держит
    верхняя граница в `pyproject.toml`, а если класс всё же переехал или сменил
    сигнатуру, сервис стартует без спанов SQL с одним предупреждением, а не падает.

    TODO: убрать обход — перейти на `SQLAlchemyInstrumentor().instrument(engine=...)` и
    снять верхнюю границу `<0.67`, когда выйдет первый релиз contrib с поддержкой
    SQLAlchemy 2.1 (ожидаемо 0.67b0): PR open-telemetry/opentelemetry-python-contrib#5120
    смёржен 2026-10-14, `_instruments` в `main` — `sqlalchemy >= 1.4.3, < 2.2.0`;
    последний релиз, 0.66b1, вышел раньше и 2.1 не объявляет.
    https://github.com/open-telemetry/opentelemetry-python-contrib/pull/5120
    """
    opt_in_stable_semconv()
    try:
        # Импорт здесь, а не наверху модуля: переезд класса — тоже `ImportError` этого вызова.
        from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer

        tracer = trace.get_tracer("opentelemetry.instrumentation.sqlalchemy")
        connections = _meter.create_up_down_counter(
            "db.client.connections.usage", unit="connections"
        )
        # Именем, а не позицией: если параметры сдвинутся, вызов упадёт `TypeError`, а
        # не отдаст счётчик в чужой параметр (следом идёт `enable_commenter`).
        EngineTracer(tracer, engine, connections_usage=connections)  # type: ignore[no-untyped-call]  # пакет без аннотаций
    except (ImportError, TypeError, AttributeError):
        logger.warning(
            "спаны запросов к базе выключены: EngineTracer несовместим", exc_info=True
        )


def instrument_pool(engine: Engine, pool_size: int, max_overflow: int) -> None:
    """Метрики пула соединений — снимками состояния, а не событиями.

    `db.client.connection.count` — observable up/down counter: колбэк снимает
    фактические `checkedout`/`checkedin` пула на момент сбора, поэтому
    соединение, выданное внутри `untraced()`, не может увести снимок в минус,
    как это было бы у событийного счётчика contrib. Предел пула —
    `db.client.connection.max` = `pool_size + max_overflow`; аргументы те же,
    что заданы `create_engine` в composition root, — иначе метрика разошлась
    бы с фактической конфигурацией. Не-`QueuePool` (например, `StaticPool`
    тестов) — тихий выход: у него нет ни `checkedout`, ни смысла в снимке.
    """
    pool = engine.pool
    if not isinstance(pool, QueuePool):
        return
    labels = {_POOL_NAME_ATTRIBUTE: CONNECTION_POOL}

    def _snapshot(_options: metrics.CallbackOptions) -> Iterator[metrics.Observation]:
        yield metrics.Observation(
            pool.checkedout(), {**labels, _STATE_ATTRIBUTE: "used"}
        )
        yield metrics.Observation(pool.checkedin(), {**labels, _STATE_ATTRIBUTE: "idle"})

    def _limit(_options: metrics.CallbackOptions) -> Iterator[metrics.Observation]:
        yield metrics.Observation(pool_size + max_overflow, labels)

    _meter.create_observable_up_down_counter(
        _CONNECTION_COUNT,
        callbacks=[_snapshot],
        unit="{connection}",
        description="Соединения с базой по состоянию снимка пула.",
    )
    _meter.create_observable_gauge(
        _CONNECTION_MAX,
        callbacks=[_limit],
        unit="{connection}",
        description="Предел пула соединений: pool_size + max_overflow.",
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

    Ставится на глобальный SDK-провайдер, поэтому зовётся после `configure_telemetry`.
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

    Без него каждый запрос к базе прохода — отдельный корневой трейс. Исключение,
    вылетевшее из прохода, спан записывает и закрывается со статусом ошибки; сбой шага,
    который проход ловит сам, чтобы выполнить следующий, отмечается на спане
    `record_current_exception`.
    """
    with error_status_span(_tracer, "review.sweep"):
        yield


@contextlib.contextmanager
def migrate_span() -> Iterator[None]:
    """Корневой спан прогона миграций: SQL-запросы миграций — его детьми.

    Открывается в `alembic/env.py` на весь online-прогон: в трейсе деплоя видно,
    сколько шли миграции и на каком операторе задержка (change
    `add-observability-stack`, задача 5.1). Исключение, вылетевшее из прогона,
    спан записывает и закрывается со статусом ошибки — упавший прогон деплоя
    тоже сигнал; исключение наружу не глотается. Без настроенного экспорта
    спаны никуда не уходят — миграции идут как раньше.
    """
    with error_status_span(_tracer, "migrate"):
        yield


def untraced() -> contextlib.AbstractContextManager[None]:
    """Блок без спанов инструментирования (SQL, httpx) — для частого фонового опроса.

    Поток событий опрашивает базу раз в секунду и живёт часами: спан на каждый запрос
    раздул бы трейс запроса до тысяч дочерних спанов. Контекст OTel — `ContextVar`,
    поэтому вход и выход должны быть в одном потоке выполнения.
    """
    return suppress_instrumentation()


def record_current_exception(exc: BaseException) -> None:
    """Отметить текущий спан ошибкой: событие `exception` и статус ERROR.

    Для исключения, которое поймано и дальше не идёт: `start_as_current_span` отмечает
    только вылетевшие из своего блока. Описание статуса — как у него.
    """
    span = trace.get_current_span()
    span.record_exception(exc)
    span.set_status(error_status(exc))


def record_run_finished(status: str) -> None:
    """Отметить терминальный исход прогона в счётчике `review_runs_total`.

    Бизнес-сигнал для алертов по исходам. Пишет тот, кто перевёл прогон в
    терминальный статус, — пайплайн (`run_review`) или выметатель
    (`sweep_stale_runs`), — поэтому счётчик считает прогоны, а не обработки
    сообщений: прикладной слой зовёт его через порт `RunOutcomeRecorder`,
    здесь — адаптер `OtelRunOutcomeRecorder`.
    """
    _review_runs_total.add(1, {"status": status})


def record_llm_call_duration(task: str, model: str, duration_seconds: float) -> None:
    """Записать длительность логического вызова модели в `review_llm_call_duration`.

    Latency видит `MeteredLlmGateway`: приложение зовёт `complete` один раз на
    логический вызов, схемные повторы и фоллбэк происходят внутри шлюза и
    свёрнуты в длительность этой точки — отдельно попытки видны слушателю
    `on_retry` и в токенах ответа (`record_llm_tokens`; аудит #89). Бакеты в
    секундах задаёт View (`_VIEWS`): на дефолтных миллисекундных
    `histogram_quantile` бесполезен.
    """
    _review_llm_call_duration.record(duration_seconds, {"task": task, "model": model})


def record_llm_tokens(task: str, model: str, tokens: int) -> None:
    """Записать расход токенов модели в счётчик `review_llm_tokens`.

    Бизнес-сигнал для алерта бюджета; по модели и задаче прогона (ревью,
    верификация). Стоимость деньгами токен не выражается — её считает
    отдельное изменение (#119), как и разбивку по типам токенов.
    """
    _review_llm_tokens.add(tokens, {"task": task, "model": model})


def error_status(exc: BaseException) -> Status:
    """Статус ошибки с именем типа, без текста исключения.

    Текст — только в событии `exception`: его чистит редакция коллектора, а описание
    статуса — нет. В тексте бывает ответ провайдера модели или значения ключей из ошибки
    базы.
    """
    return Status(StatusCode.ERROR, type(exc).__name__)


@contextlib.contextmanager
def error_status_span(
    tracer: trace.Tracer, name: str, **kwargs: Any
) -> Iterator[trace.Span]:
    """Текущий спан, который при исключении закрывается со статусом `error_status`.

    `start_as_current_span` сам пишет в описание статуса текст исключения — это
    отключено, статус ставится по типу. Событие `exception` для `Exception` пишет сам
    спан; `KeyboardInterrupt` и `SystemExit` он не видит — их отмечаем здесь целиком.
    """
    with tracer.start_as_current_span(name, set_status_on_exception=False, **kwargs) as span:
        try:
            yield span
        except Exception as exc:
            span.set_status(error_status(exc))
            raise
        except BaseException as exc:
            record_current_exception(exc)
            raise


def current_trace_id() -> str | None:
    """Trace id текущего спана — 32 строчных hex; `None`, если спана нет."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return trace.format_trace_id(span_context.trace_id)


class _OtelStageSpan:
    def __init__(self, span: trace.Span, token: Token[Context], stage: ReviewRunStage) -> None:
        self._span = span
        self._token = token
        self._stage = stage
        self._started = time.monotonic()

    def end(self, error_code: str | None) -> None:
        if error_code is not None:
            self._span.set_attribute("error.type", error_code)
            self._span.set_status(Status(StatusCode.ERROR, error_code))
        self._span.end()
        otel_context.detach(self._token)
        _review_stage_duration.record(
            time.monotonic() - self._started, {"stage": self._stage.value}
        )


class OtelStageTracer:
    """Адаптер `StageTracer`: спан `review.<этап>` с атрибутом `review_run_id`.

    Спан этапа делается текущим, пока этап идёт: запросы httpx и SQLAlchemy этапа
    ложатся в трейс его детьми. Шаги идут строго по одному, поэтому снятие контекста
    в `end` не нарушает порядок. Тем же адаптером пишется гистограмма
    `review_stage_duration{stage}` — длительность этапа по монотонным часам.
    """

    def start(self, review_run_id: UUID, stage: ReviewRunStage) -> _OtelStageSpan:
        span = _tracer.start_span(
            f"review.{stage.value}",
            attributes={REVIEW_RUN_ID: str(review_run_id), "review.stage": stage.value},
        )
        token = otel_context.attach(trace.set_span_in_context(span))
        return _OtelStageSpan(span, token, stage)


class OtelRunOutcomeRecorder:
    """Адаптер `RunOutcomeRecorder`: исход прогона — в счётчик `review_runs_total`.

    Передаётся composition root-ом (`run_worker`) в пайплайн и выметатель:
    запись делает тот, кто перевёл прогон в терминальный статус, поэтому
    повторная доставка терминального прогона и сообщение без прогона счётчик
    не трогают.
    """

    def run_finished(self, status: str) -> None:
        record_run_finished(status)
