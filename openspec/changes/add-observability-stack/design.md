# Design

## Context

Трейсинг уже стоит: `app/infrastructure/telemetry.py` (#92) даёт SDK-провайдер, спаны запросов
FastAPI, httpx и SQL (`EngineTracer`), спаны этапов и очереди, `X-Trace-ID`. Экспорта нет:
`build_tracer_provider` понимает только `console`. Логов как настройки тоже нет — uvicorn пишет
свой формат, приложение молчит; обработчик `500` пишет строку без стектрейса (его пишет uvicorn —
дублем без `trace_id`). Аудит #92 зафиксировал, что всплывёт с экспортом: SSE-поток плодит
SQL-спаны и ERROR на отключение клиента, чувствительные данные в атрибутах спанов, нет признака
повторной доставки. Спека `observability` уже требует: страж «данные продукта — из базы», пустой
пропагатор, выключенный OTel-лог FastAPI (`telemetry={"auto_configure": False, "logs": False}` в
`app/api/factory.py`).

Решения по стеку и путь логов (stdout → `file_log`, а не нативный OTel Logs SDK, который в Python
ещё Development) приняты в #89 — здесь только «как».

## Goals / Non-Goals

**Goals:**

- Включение экспорта трейсов, метрик и логов настройкой окружения, без правки прикладного кода.
- Один и тот же набор конфигов коллектора и хранилищ в локальном compose и на стенде.
- Логи: JSON со trace-контекстом, собираемый stdlib `logging`, с одной точкой настройки.

**Non-Goals:**

- Нативный OTel Logs SDK и OTLP для логов — TODO в `configure_logging()`, отдельное изменение,
  когда SDK станет Stable.
- Tail sampling, алерты (Alertmanager, бюджеты LLM, диск) и прод-инфраструктура DevOps: этот change
  отдаёт конфиги, развёртывание и алерты — их задача.
- Контракт API и схема базы не меняются: ручки, ответы, миграции БД — нет.

## Decisions

### 1. Логи: один модуль `app/infrastructure/logging.py`

`configure_logging(service_name)` ставит на root один `StreamHandler(sys.stdout)` с JSON-форматтером
и `TraceContextFilter`. Точки вызова — там же, где настраивается трассировка: `create_app`
(фабрика приложения, до первого запроса), `app/worker/__main__.py` и `alembic/env.py` до `fileConfig`
— `fileConfig` остаётся с `disable_existing_loggers=False` и наш хендлер не глушит.

- **Формат — плоский JSON по модели OTel LogRecord**: `timestamp` (RFC 3339, UTC), `severity`
  (уровень), `message` (тело), `trace_id` / `span_id` / `trace_flags` (lowercase hex, из текущего
  спана), `service.name`, остальные поля из `extra` — атрибутами на верхнем уровне (`review_run_id`).
  Плоский, а не вложенный: так запись читается в Loki без разворачивания, а имена полей совпадают с
  теми, что ждёт `json_parser` коллектора (блоки `trace: {}` и `severity` — имена по умолчанию).
- **`TraceContextFilter`** берёт `trace_id`/`span_id`/`trace_flags` из
  `trace.get_current_span()` и переносит в record; вне спана поля просто отсутствуют. Бизнес-контекст
  код передаёт `extra={"review_run_id": …}` — фильтр и форматтер не знают его имён.
- **Дубль uvicorn** отсекает фильтр на логгере `uvicorn.error` по сообщению «Exception in ASGI
  application» — тем же приёмом, что `AuthQueryRedaction` в `app/api/log_redaction.py`. Логгеры
  `uvicorn.*` направляются на наш хендлер, чтобы весь stdout контейнера был JSON.
- **Стектрейс `500`** — `logger.error("Необработанное исключение", exc_info=error, extra={…метод,
  путь…})` в `_internal_error_handler` (`app/api/errors.py`): запись пишется внутри спана запроса,
  её `trace_id` = `X-Trace-ID` (проверено экспериментом в #89); текущая версия без `exc_info`
  удаляется, `telemetry={"logs": False}` остаётся.
- В `configure_logging()` — TODO: при Stable OTel Logs перейти на `LoggingHandler` из
  `opentelemetry-instrumentation-logging` (пакет уже в зависимостях) и убрать сбор stdout.

Альтернатива — structlog или свой фасад над логгерами — отклонена: мост OTel цепляется к stdlib
`logging`, а обёртки нарушают правило «`logging.getLogger(__name__)` без посредников».

### 2. Экспорт: список в `build_tracer_provider`

`OTEL_TRACES_EXPORTER` разбирается по спецификации OTel: значения через запятую, каждое `strip()`
и `lower()`, дубликаты не плодят процессоры. `console` → `BatchSpanProcessor(ConsoleSpanExporter())`,
`otlp` → `BatchSpanProcessor(OTLPSpanExporter())` из
`opentelemetry-exporter-otlp-proto-http` (endpoint читает из `OTEL_EXPORTER_OTLP_ENDPOINT` сам).
Неподдержанное значение — одно предупреждение на значение, остальные работают. Новая зависимость —
только `opentelemetry-exporter-otlp-proto-http`.

- **Сэмплирование** — явно `parentbased_always_on` (дефолт SDK, закреплён кодом).
- **Health-check** исключается из трейсинга опцией исключения встроенной телеметрии FastAPI; если
  опция не покроет спан — процессор, дропающий спаны `/health` (образец — `QueryRedaction`).
- **`OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`** — лимит длины значений атрибутов в
  `build_tracer_provider` (читаем переменную, дефолт ограничивает раздувание `url.full` и текстов
  ошибок).
- **Resource**: `Resource.create({SERVICE_NAME: …})` уже мержит `OTEL_SERVICE_NAME` и
  `OTEL_RESOURCE_ATTRIBUTES` — `service.version` (git sha) и `deployment.environment.name` задаются
  окружением в compose и `infra/`, код не дублирует.

### 3. Метрики: SDK `MeterProvider` рядом с трейсами

`configure_tracing` (получит имя `configure_telemetry`, прежнее останется вызовом) ставит глобальный
`MeterProvider` с `PeriodicExportingMetricReader(OTLPMetricExporter())`. Встроенная телеметрия
FastAPI, как и с трейсами, берёт глобальный провайдер — HTTP-метрики появляются сами. `prometheus_client`
и ручка `/metrics` не вводятся: Prometheus скрейпит exporter-порт коллектора.

Ручные метрики пишут инфраструктурные адаптеры, домен и application про OTel не узнают (запрет в
`lint-imports` уже стоит): длительность и исходы этапов — адаптер рядом с `OtelStageTracer`
(счётчик `review_runs_total{status}`, гистограмма `review_stage_duration{stage}`); токены и
стоимость — адаптер LLM-шлюза, где usage уже есть; страж «данные продукта — из базы» не меняется.
RabbitMQ — плагин `rabbitmq_prometheus` (порт 15692), Postgres — контейнер
`prometheus-community/postgres-exporter`: оба настраиваются в compose и `infra/`, кода не касаются.

### 4. Коллектор: конфиг в репозитории, один на локальный стек и стенд

Каталог `infra/observability/` с конфигами: `otelcol.yaml` (receivers: `otlp` grpc/http, `file_log`
с `json_parser` и блоками `trace: {}`, `severity`; processors: `memory_limiter`, `batch`,
`transform` для редакции; exporters: Loki, Tempo — `otlphttp`, Prometheus — exporter-порт),
`loki.yaml`, `tempo.yaml`, `prometheus.yml`, provisioning Grafana (datasources с переходом лог ↔
трейс по `trace_id`). Образы фиксируются тегами; `otel/opentelemetry-collector-contrib` — beta для
`file_log`, поэтому при обновлении образа читаются breaking changes.

- **Редакция (второй рубеж после SDK)** — OTTL-правила `transform`: `url.query`/`url.full` httpx-спанов,
  значения `db.*` и description статуса SQL-спанов (в DETAIL psycopg — значения ключей), message и
  stacktrace события `exception` на спанах обработки сообщения (там текст ответа провайдера) —
  заменяются маркером `<скрыто>`, атрибут остаётся. Тип исключения сохраняется: диагноз в трейсе
  остаётся, секреты — нет; полный стектрейс доступен в логе нашей записи об ошибке.
- **Логи из stdout**: `file_log` читает лог-файлы контейнеров API, воркера и `migrate` (docker
  logging driver `json-file`); различия путей между хостом локального стека и стенда — переменные
  запуска коллектора, не разные конфиги.
- **Compose**: пять сервисов под профилем `observability` (поднял — получил стек локально; без
  профиля — стек не мешает тем, кто его не просил), `infra/` — те же конфиги через
  `docker_container` в существующей сети. Grafana локально — без логина, на стенде — учётка из
  переменных (остаётся DevOps).

### 5. SSE: подавление SQL-спанов и не-ошибка на отключение

Из трёх вариантов issue (исключить путь из трейсинга / подавить SQL-спаны / спан на итерацию)
берём **подавление SQL-спанов в цикле опроса**: серверный спан потока сохраняется (видно
длительность просмотра и сам факт SSE в трейсе), трейс не растёт с длительностью, `X-Trace-ID`
потока остаётся настоящим trace id. Исключение пути из трейсинга лишило бы спан и `trace_id`
связи с логами; спан на итерацию меняет сотни SQL-спанов на сотни своих.

Механизм — `SpanSuppression`: генератор потока ставит contextvar-флаг на время чтений
(`run_in_threadpool` наследует контекст), span-процессор в `on_start` видит флаг в parent-context и
дропает спан из экспорта. Тот же процессор в `on_end` нормализует статус серверного спана SSE-пути
при отключении клиента (`ClientDisconnect`/`CancelledError`) — без ERROR; это инфраструктура, роуты
OpenTelemetry не импортируют (страж спеки).

### 6. Красные флаги очереди и миграций

- `consuming_span` получает флаг `redelivered` из `basic.deliver` (pika отдаёт его в method) и
  ставит `messaging.rabbitmq.message.redelivered` — по semconv RabbitMQ, всегда булевым значением.
- `alembic/env.py` в online-режиме: `configure_telemetry(MIGRATE_SERVICE)` с
  `service_name="dmc-268-migrate"`, корневой спан `migrate` на `run_migrations_online`,
  `instrument_engine( engine)` — SQL-миграции детьми, `shutdown_tracing()` в конце. Без
  `OTEL_TRACES_EXPORTER` поведение миграций не меняется.

## Risks / Trade-offs

- [EngineTracer — внутренний класс пакета] → уже принят в #92; риск не растёт: правки не касаются
  SQL-инструментирования, тест сквозного трейса остаётся стражем.
- [`file_log` — beta, операторы stanza вне гарантий стабильности] → версия образа зафиксирована,
  разбор минимальный (`json_parser`, `trace: {}`, `severity`), обновление образа — отдельная задача
  с чтением breaking changes.
- [Редакция в коллекторе остаётся без unit-теста в Python] → тест конфига на примерах из аудита
  (#89): примеры спанов прогоняются через правила `transform` (OTTL применим в тесте) плюс валидация
  конфига самим коллектором при поднятии стека.
- [Пять контейнеров стека на стенде — ресурсы VPS] → лимиты памяти в конфигах вынесены в
  переменные; прод-размеры — DevOps.
- [Очередь `BatchSpanProcessor` (2048) при всплеске] → подавление SQL-спанов SSE убирает главный
  генератор; tail sampling — отдельное изменение, если объём вырастет.
- [Плоский JSON логов — не каноническая сериализация OTLP] → поля по модели OTel, поэтому переход
  на нативные OTel Logs меняет транспорт, а не вид записи; Loki-записи до и после перехода
  выглядят одинаково.

## Migration Plan

Всё включается окружением: сервису нужен `OTEL_TRACES_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_ENDPOINT`
и `OTEL_RESOURCE_ATTRIBUTES`. Без них поведение как в #92 (спаны никуда не уходят, `X-Trace-ID`
работает). Откат — вернуть окружение; код и контракт не меняются. Стек — отдельные контейнеры,
отключаются независимо от сервиса.

## Open Questions

- Точная версия тега образов стека (collector, Loki, Tempo, Prometheus, Grafana) — фиксируется при
  реализации по актуальным релизам; на подход не влияет.
- Место счётчика терминальных статусов прогона (адаптер воркера или LLM-шлюза) — деталь задач,
  решается при написании теста.
