# Proposal

## Why

`add-otel-tracing` (#84, PR #92) дал трейс «вебхук → очередь → этапы воркера», но без экспорта: спаны
уходят только в консоль для отладки, логи API и воркера лежат в stdout контейнеров без структуры и
не связаны с трейсами, метрик нет совсем. Команда не может ответить на вопросы «что случилось с
прогоном с этим `trace_id`», «на каком этапе тормозит», «сколько жрёт модель». Часть проблем из
аудита #92 (раздувание трейса потоком SSE, чувствительные данные в спанах, потеря признака
повторной доставки) проявляется только с включённым экспортом — чинить их нужно там же.

## What Changes

- **Логи — JSON в stdout через stdlib `logging`.** Один модуль инфраструктуры
  `configure_logging()`: JSON-форматтер по модели данных OTel (`trace_id`, `span_id`,
  `trace_flags` на верхнем уровне, lowercase hex, уровень → severity, `service.name` → resource) и
  `Filter`, берущий trace-контекст из текущего спана и бизнес-контекст (`review_run_id`) из
  `contextvars`. Контекст — только через `extra`/`Filter`, не вклейкой в текст. Нативный OTel Logs
  SDK в Python ещё не Stable — сейчас стабильный путь через stdout, в `configure_logging()`
  остаётся TODO на переход.
- **Одна запись со стектрейсом на `500`.** `_internal_error_handler` пишет `logger.error(...,
  exc_info=error)` с `trace_id`, методом и путём без query; дубль uvicorn («Exception in ASGI
  application») отсекается фильтром. Проверено экспериментом (issue #89): наша запись пишется
  внутри спана запроса, строка uvicorn — после закрытия спана, без `trace_id`.
- **Экспорт спанов — список по спецификации OTel.** `OTEL_TRACES_EXPORTER` разбирается как список
  через запятую (каждый экспортёр отдельно), поддержаны `console` и `otlp` (OTLP/HTTP → коллектор).
  Сэмплирование 100% (`parentbased_always_on`), health-check вне трейсинга,
  `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT` задан.
- **Коллектор и стек — одинаковые конфиги локально и на проде.** OTel Collector
  (`otel/opentelemetry-collector-contrib`, версия зафиксирована): приём OTLP, сбор логов из stdout
  (`file_log`), `memory_limiter`, `batch`, редакция чувствительных данных (`url.query`, `url.full`,
  `db.*`, описание статуса SQL-спанов, события `exception`). Хранилища: Loki — логи, Tempo —
  трейсы, Prometheus — метрики, Grafana — интерфейс с переходом лог ↔ трейс по `trace_id`.
  Локально — те же пять контейнеров в compose, на проде — в `infra/`.
- **Метрики — OTel SDK → коллектор → Prometheus**, без `prometheus_client` и ручки `/metrics`.
  HTTP-метрики даёт встроенная телеметрия FastAPI. Ручные — только бизнес-сигналы для алертов:
  прогоны по статусам, длительность этапов, токены и стоимость LLM. Очередь — плагин
  `rabbitmq_prometheus`, Postgres — свой экспортёр.
- **Из аудита #92:** SSE-поток `GET /reviews/{id}/events` не раздувает трейс (сегодня десять минут
  просмотра — сотни SQL-спанов-детей в одном трейсе) и штатное отключение клиента не закрывает спан
  с ERROR; на спане обработки сообщения появляется признак повторной доставки
  (`messaging.rabbitmq.message.redelivered`).
- **Миграции в трейсе:** `alembic upgrade head` открывает свой корневой спан (сервис
  `dmc-268-migrate`), SQL-миграции — его дети; в Tempo видно, сколько миграции идут при деплое.
- **Resource:** `service.version` (git sha) и `deployment.environment.name` через
  `OTEL_RESOURCE_ATTRIBUTES`.
- **Документация:** раздел о наблюдаемости в `docs/BACKEND_ARCHITECTURE.md` (сейчас «Prometheus и
  Grafana не нужны»), метрики в `docs/testing/TEST_PLAN.md`, правила логов в
  `.agents/rules/backend.md` (раздел «Наблюдаемость»).

## Capabilities

### New Capabilities

_Нет: всё изменение достраивает существующую capability `observability`, введённую #92._

### Modified Capabilities

- `observability`: требования про логи (JSON с trace-контекстом, стектрейс `500`), экспорт спанов
  (список экспортёров, `otlp`, сэмплирование), коллектор и стек хранилищ, метрики, SSE и признак
  повторной доставки, миграции в трейсе, resource-атрибуты. Граница «телеметрия — для команды,
  данные продукта — из базы» не меняется и распространяется на метрики.

## Impact

- **Код:** новый модуль настройки логов в `app/infrastructure/`; `app/infrastructure/telemetry.py`
  (список экспортёров, OTLP, resource, подавление SQL-спанов SSE); `app/api/errors.py`
  (`exc_info`); `app/api/review_events.py` (отключение клиента); `app/infrastructure/queue/rabbitmq.py`
  (redelivered); `alembic/env.py` (спан миграций); входы API и воркера (вызов `configure_logging()`).
- **Инфраструктура:** `docker-compose.yml` (пять контейнеров наблюдаемости, тот же профиль,
  что на проде), `infra/` (Terraform: те же контейнеры на стенде), конфиги коллектора, Loki,
  Tempo, Prometheus, Grafana в репозитории.
- **Зависимости:** `opentelemetry-exporter-otlp-proto-http` (трейсы и метрики OTLP/HTTP);
  версии образов стека фиксируются.
- **Не меняется:** контракт API (`docs/openapi/openapi.json` не трогаем), схема базы (миграций
  нет), ответы и поведение прогона.
- **Деплой:** сервисам нужен только `OTEL_EXPORTER_OTLP_ENDPOINT` — код не меняется при включении
  экспорта; прод-инфраструктура и алерты (Alertmanager и т. п.) — задача DevOps, этот change
  отдаёт готовые конфиги.
