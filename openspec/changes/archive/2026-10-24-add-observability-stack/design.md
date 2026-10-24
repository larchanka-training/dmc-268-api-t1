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

`configure_telemetry` (бывший `configure_tracing`; прежнее имя при ревью PR #130 удалено — вызовы
обновлены) ставит глобальный
`MeterProvider`; экспорт гейтит `OTEL_METRICS_EXPORTER` — тот же список через запятую, что у
`OTEL_TRACES_EXPORTER`: `none` по умолчанию, поддержаны `otlp` и `console`, мусорное значение — одно
предупреждение при старте. Без гейта SDK сам ставит `otlp` и стучался бы в localhost:4318, поэтому
по умолчанию ридеров нет: метрики собираются, но никуда не уходят и ошибок в логе нет — в духе
требования «Экспорт спанов включается явно». `OTEL_METRICS_EXPORTER=console` печатает метрики
многострочными не-JSON блоками в stdout, которые `json_parser` Loki не разберёт, — для локальной
отладки, не для контуров. Встроенная телеметрия FastAPI, как и с трейсами, берёт
глобальный провайдер — HTTP-метрики появляются сами. `prometheus_client` и ручка `/metrics` не
вводятся: Prometheus скрейпит exporter-порт коллектора.

Ручные метрики пишут инфраструктурные адаптеры, домен и application про OTel не узнают (запрет в
`lint-imports` уже стоит): длительность и исходы этапов — адаптер рядом с `OtelStageTracer`
(счётчик `review_runs_total{status}`, гистограмма `review_stage_duration{stage}`); токены и
стоимость — адаптер LLM-шлюза, где usage уже есть; страж «данные продукта — из базы» не меняется.
RabbitMQ — плагин `rabbitmq_prometheus` (порт 15692). Метрики базы снимает сам коллектор
receiver-ом `postgresql` — отдельного контейнера postgres-exporter нет (возврат к решению #89,
закреплено ревью PR #130): один конвейер метрик без лишнего контейнера, у коллектора роль
только-чтение `dmc268_monitor` с привилегией `pg_monitor` (создаёт миграция 0013), тяжёлые запросы
— из `pg_stat_statements`. Экономия памяти тут не главный аргумент — главный один конвейер и
минимальные права на базу.

### 4. Коллектор: конфиг в репозитории, один на локальный стек и стенд

Каталог `infra/observability/` с конфигами: `otelcol.yaml` (receivers: `otlp` grpc/http, `file_log`
с `json_parser` и блоками `trace: {}`, `severity`, `postgresql`; processors: `memory_limiter`,
`batch`, `filter` чужих сервисов, `transform` для редакции трейсов и логов; exporters: Loki, Tempo
— `otlphttp`, Prometheus — exporter-порт), `loki.yaml`, `tempo.yaml`, `prometheus.yml`,
provisioning Grafana (datasources с переходом лог ↔ трейс по `trace_id`, дашборд «Обзор dmc-268»
с панелями по бизнес-сигналам из решения 3 и по node_exporter/cAdvisor). Образы фиксируются
дайджестами манифест-листов (`имя@sha256:…`); `otel/opentelemetry-collector-contrib` — beta для
`file_log`, поэтому при обновлении образа читаются breaking changes.

- **Редакция (второй рубеж после SDK)** — OTTL-правила `transform`: `url.query`/`url.full` httpx-спанов,
  значения `db.*` и description статуса SQL-спанов (в DETAIL psycopg — значения ключей), message и
  stacktrace события `exception` на спанах обработки сообщения (там текст ответа провайдера) —
  заменяются маркером `<скрыто>`, атрибут остаётся. Тип исключения сохраняется: диагноз в трейсе
  остаётся, секреты — нет; полный стектрейс доступен в логе нашей записи об ошибке.
- **Логи из stdout**: `file_log` читает лог-файлы контейнеров API, воркера и `migrate` (docker
  logging driver `json-file`); различия путей между хостом локального стека и стенда — переменные
  запуска коллектора, не разные конфиги.
- **Compose**: семь сервисов под профилем `observability` — коллектор, Loki, Tempo, Prometheus,
  Grafana, node_exporter (сервер: CPU, память, диски) и cAdvisor (контейнеры) — поднял, получил
  стек локально; без профиля стек не мешает тем, кто его не просил. `infra/` — те же конфиги через
  `docker_container` в существующей сети. Grafana локально — без логина, на стенде — учётка из
  переменных (остаётся DevOps).

### 5. SSE: подавление SQL-спанов и не-ошибка на отключение

Из трёх вариантов issue (исключить путь из трейсинга / подавить SQL-спаны / спан на итерацию)
берём **подавление SQL-спанов в цикле опроса** общего потока `GET /api/v1/events`
(`stream_events` в `app/api/events.py`): серверный спан потока сохраняется (видно
длительность просмотра и сам факт SSE в трейсе), трейс не растёт с длительностью, `X-Trace-ID`
потока остаётся настоящим trace id. Исключение пути из трейсинга лишило бы спан и `trace_id`
связи с логами; спан на итерацию меняет сотни SQL-спанов на сотни своих.

Механизм — переиспользованное подавление `untraced` (`app/infrastructure/telemetry.py`):
contextvar-флаг поверх `suppress_instrumentation`, который уже проверяют инструментирования SQL и
httpx, — отдельный span-процессор дублировал бы готовое. Включение — из инфраструктурной обвязки,
роуты OpenTelemetry не импортируют (страж спеки): `stream_events` получает `untraced_uow_factory`
зависимостью (`get_untraced_unit_of_work_factory` → `UntracedUnitOfWork`), и каждое чтение цикла
идёт внутри `telemetry.untraced()`; `run_in_threadpool` наследует контекст. Нормализация статуса в
`on_end` не понадобилась: при отключении клиента (`ClientDisconnect`/`CancelledError`) `_EventStream`
в `app/api/events.py` штатно досылает финальную часть ответа, а `CancelledError` отменённого
генератора поглощает collapsing task group Starlette — исключение до спана запроса не доходит, и
он закрывается без ERROR.

### 6. Красные флаги очереди и миграций

- `consuming_span` получает флаг `redelivered` из `basic.deliver` (pika отдаёт его в method) и
  ставит `messaging.rabbitmq.message.redelivered` — по semconv RabbitMQ, всегда булевым значением.
- `alembic/env.py` в online-режиме: `configure_telemetry(MIGRATE_SERVICE)` с
  `service_name="dmc-268-migrate"`, корневой спан `migrate` на `run_migrations_online`,
  `instrument_engine( engine)` — SQL-миграции детьми, `shutdown_tracing()` в конце. Без
  `OTEL_TRACES_EXPORTER` поведение миграций не меняется.

### 7. Правки ревью PR #130

Решения, изменившиеся или уточнившиеся при ревью; всё остальное выше — как было задумано.

- **Логи httpx/httpcore — на WARNING.** Root-логгер на INFO сливал URL исходящих запросов
  (имя приватного репозитория, пути файлов); в `configure_logging` логгеры `httpx` и `httpcore`
  переводятся на WARNING.
- **Редакция логов в коллекторе.** `transform/redact-logs` (`log_statements`): атрибуты
  `exception.stacktrace`/`exception.message` записи — маркером, тело записи, несущей исключение,
  — целиком; тело записи — текст `message` (оператор `move`), `color_message` uvicorn удаляется
  `delete_key` (тихо пропускает отсутствие ключа).
- **Фильтр логов по `service.name`.** Коллектор читает `*-json.log` всех контейнеров хоста и
  остаётся под root — иначе лог-файлы Docker не прочитать; чужие JSON-логи отсекает
  `filter/foreign-services` по `service.name` ∈ {dmc-268-api, dmc-268-worker, dmc-268-migrate}
  (у OTLP-логов имя в ресурсе — условие покрывает оба случая).
- **Переход лог → трейс — по structured metadata.** `trace_id` уже лежит в structured metadata
  Loki (блок `trace: {}` в `json_parser`), derived field берёт его лейблом (`matcherType: label`):
  старый регэксп по телу не совпадал из-за пробела после двоеточия в `json.dumps`.
- **Счётчик соединений — снимок пула.** Событийный счётчик contrib (`db.client.connections.usage`)
  пропускает события внутри `untraced()` и дрейфует; вместо него `instrument_pool` снимает
  `checkedout`/`checkedin` observable-колбэками (`db.client.connection.count` + gauge
  `db.client.connection.max`), а contrib-счётчик гасится `View(DropAggregation)` — передать
  `None` в `EngineTracer` нельзя, его листенеры зовут `.add`.
- **Счётчик прогонов — на терминальном переходе.** Порт `RunOutcomeRecorder` (Protocol + noop
  в `app/application/ports`) получают `run_review` и `sweep_stale_runs`; запись делает тот, кто
  перевёл прогон в терминальный статус, — повторная доставка терминального прогона и сообщение
  без прогона счётчик не трогают. Из `build_review_handler` запись убрана.
- **`review_run_id` в логах — через ContextVar.** Фильтр хендлера берёт идентификатор из
  контекста (`review_run_context` в `logging.py`), контекст ставит `handle()` воркера вокруг
  `run_review`; из текстов сообщений идентификатор убран, sweep-путь несёт его `extra`.
- **Бакеты гистограмм — через View.** `review_stage_duration` и `review_llm_call_duration`
  (новая — latency каждого логического вызова модели, пишет `MeteredLlmGateway`) получают
  `ExplicitBucketHistogramAggregation` [0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300] с:
  на дефолтных миллисекундных бакетах `histogram_quantile` бесполезен.
- **`resource_to_telemetry_conversion` у exporter prometheus.** Без неё `service.name` прячется
  в `exported_job` и все ряды выглядят как `job="collector"`; с ней лейбл `job` = `service.name` —
  ряды и алерты различают сервисы.
- **Дашборд из репозитория.** Provider + JSON «Обзор dmc-268» в provisioning Grafana: RPS/5xx,
  p50/p95 HTTP и вызова модели, очередь `review_jobs`, прогоны по статусам, этапы, соединения
  приложения и базы, CPU/RAM/диски сервера и контейнеров; лейбл панелей — `job` = `service.name`.
- **Query access-лога скрыт на всех путях.** `AccessQueryRedaction` (бывший
  `AuthQueryRedaction`) режет query в access-логе без условия по префиксу: поисковые и фильтровые
  query кабинета в логе не нужны нигде; в спанах скрытие остаётся на путях входа (`/api/v1/auth/`).
- **Health-check — вне трейсинга.** Опция `exclude` встроенной телеметрии FastAPI исключает
  `/health` целиком: спана нет, `X-Trace-ID` ответа — случайное значение без трейса.
- **Метрики базы — receiver `postgresql`** вместо postgres-exporter (см. решение 3).

## Risks / Trade-offs

- [EngineTracer — внутренний класс пакета] → уже принят в #92; риск не растёт: правки не касаются
  SQL-инструментирования, тест сквозного трейса остаётся стражем.
- [`file_log` — beta, операторы stanza вне гарантий стабильности] → версия образа зафиксирована,
  разбор минимальный (`json_parser`, `trace: {}`, `severity`), обновление образа — отдельная задача
  с чтением breaking changes.
- [Редакция в коллекторе остаётся без unit-теста в Python] → тест конфига на примерах из аудита
  (#89): примеры спанов прогоняются через правила `transform` (OTTL применим в тесте) плюс валидация
  конфига самим коллектором при поднятии стека.
- [Семь контейнеров стека на стенде — ресурсы VPS] → лимиты памяти в конфигах вынесены в
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

- CI-валидация конфигов стека (коллектор, Loki, Tempo) — вынесена в issue
  [#140](https://github.com/larchanka-training/dmc-268-api-t1/issues/140): локально конфиг
  коллектора валидирует тест под маркером `docker`, в CI он не гоняется, битый OTTL-стейтмент
  доезжает до стенда.
- Решено при реализации: версии образов зафиксированы дайджестами манифест-листов
  (`имя@sha256:…`) в compose и `main.tf`; место счётчика терминальных статусов — терминальный
  переход через порт `RunOutcomeRecorder` (см. решение 7), а не адаптер в
  `build_review_handler`.
