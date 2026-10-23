# Tasks

## 1. Логи — JSON со trace-контекстом

- [x] 1.1 Модуль `app/infrastructure/logging.py`: чистая функция `record_to_fields(record)` (поля
  JSON: `timestamp`, `severity`, `message`, `trace_id`/`span_id`/`trace_flags` lowercase hex,
  `service.name`, атрибуты из `extra`), `TraceContextFilter` (контекст из текущего спана) и
  `configure_logging(service_name)` c TODO на нативный OTel Logs. Вызовы — в `create_app`,
  `app/worker/factory.py` (`run_worker` — вход `__main__.py` остаётся тонким), `alembic/env.py`.
  Тест: запись внутри спана несёт его `trace_id`, `span_id` и `review_run_id` из `extra`; запись
  вне спана — без trace-полей и не теряется
- [x] 1.2 Фильтр дубля uvicorn `UvicornDuplicateFilter` («Exception in ASGI application») на
  логгере `uvicorn.error`; логгеры `uvicorn.*` направлены на JSON-хендлер. Тест: дубль
  отсекается, остальные записи `uvicorn.error` проходят
- [x] 1.3 `_internal_error_handler` (`app/api/errors.py`): `logger.error(..., exc_info=error,
  extra={метод, путь без query})`. Тест под настоящим uvicorn: на `500` в логе ровно одна запись
  со стектрейсом, её `trace_id` равен `X-Trace-ID` ответа
- [x] 1.4 Тест запретного списка: в записи лога нет тел запросов к модели, кода из диффа, токенов
  доступа и query путей входа — на примерах вызова модели, ответа GitHub и callback входа

## 2. Экспорт спанов — список экспортёров

- [x] 2.1 Зависимость `opentelemetry-exporter-otlp-proto-http` через `uv`; чистая функция
  `parse_exporters(value) -> list[str]` (запятая, пробелы, регистр, дубликаты, пустое значение).
  Тест разбора: `console,otlp` → оба; `console,zipkin` → `console` + мусор; `""` → пусто
- [x] 2.2 `build_tracer_provider`: процессор на каждый экспортёр (`console`, `otlp` —
  `OTLPSpanExporter`), одно предупреждение на неподдержанное значение, явно
  `parentbased_always_on`, лимит длины атрибутов из `OTEL_ATTRIBUTE_VALUE_LENGTH_LIMIT`. Тест:
  конфигурация провайдера по `OTEL_TRACES_EXPORTER=console,otlp` и по мусорному значению
- [x] 2.3 Health-check вне трейсинга: опция исключения встроенной телеметрии FastAPI (или
  дропающий процессор, если опция не покрывает). Тест: запрос `/health` не создаёт спана
- [x] 2.4 Resource: `service.version` и `deployment.environment.name` из
  `OTEL_RESOURCE_ATTRIBUTES` мержатся к `service.name` из кода. Тест: спан несёт все три
  атрибута при заданном окружении

## 3. Из аудита #92 — SSE и очередь

- [x] 3.1 Подавление SQL-спанов опроса в цикле общего потока `GET /api/v1/events` — ручка
  `stream_events` в `app/api/events.py`, цикл опроса — `run_feed_events` (бывший
  `review_run_events`). Механизм — существующий contextvar-флаг `untraced`
  (`UntracedUnitOfWork` зависимостью `untraced_uow_factory`), второго процессора не заводить.
  Тест: «десять минут» просмотра (прокрутка цикла с чтениями) не добавляют SQL-спанов в трейс,
  спан потока один
- [ ] 3.2 Статус серверного спана общего потока `GET /api/v1/events` (`stream_events`) при
  отключении клиента (`ClientDisconnect`/`CancelledError`) — без ERROR: уход клиента штатно
  закрывает ответ (`_EventStream` в `app/api/events.py`). Тест: отключение до терминального
  статуса закрывает спан без ошибки
- [x] 3.3 `consuming_span(..., redelivered: bool)`: атрибут
  `messaging.rabbitmq.message.redelivered` из `basic.deliver`. Тест: повторная доставка
  (unack + redeliver на настоящем брокере) даёт спан с `redelivered=true`, первая — `false`

## 4. Метрики — OTel SDK в Prometheus

- [ ] 4.1 `configure_telemetry` (переименование `configure_tracing`, точки вызова те же):
  глобальный `MeterProvider` с `PeriodicExportingMetricReader(OTLPMetricExporter())`. Тест:
  запросы в API дают HTTP-метрики встроенной телеметрии FastAPI; ручки `/metrics` нет,
  `uv run python scripts/export_openapi.py --check` без изменений
- [ ] 4.2 Адаптер бизнес-метрик рядом с `OtelStageTracer`: счётчик `review_runs_total{status}`,
  гистограмма `review_stage_duration{stage}`; токены и стоимость LLM — в адаптере LLM-шлюза, где
  известен usage. Тест: упавший прогон вырос в счётчике по статусу, длительность этапа — в
  гистограмме; домен и application не импортируют OTel (страж `lint-imports`)

## 5. Миграции — в трейсе деплоя

- [ ] 5.1 `alembic/env.py` (online-режим): `configure_telemetry` с `service_name` =
  `dmc-268-migrate`, корневой спан `migrate`, `instrument_engine(engine)`, `shutdown_tracing()`
  в конце. Тест: `alembic upgrade head` даёт трейс с SQL-детьми и не меняет результат (миграционные
  тесты зелёные, `uv run alembic heads` — одна ревизия)

## 6. Коллектор и стек — одинаково локально и на проде

- [ ] 6.1 Каталог `infra/observability/`: `otelcol.yaml` (приём OTLP, `file_log` с `json_parser`
  `trace: {}` `severity`, `memory_limiter`, `batch`, `transform`), конфиги Loki, Tempo, Prometheus,
  provisioning Grafana; версии образов зафиксированы. Проверка: `docker compose --profile
  observability up` поднимает пять контейнеров, коллектор стартует без ошибок конфига
- [ ] 6.2 OTTL-правила редакции: `url.query`/`url.full` httpx, значения `db.*` и description
  статуса SQL-спанов, message и stacktrace события `exception` на спанах обработки — маркер
  `<скрыто>`, атрибут остаётся. Тест на примерах из аудита #89 (DETAIL psycopg с ключами, текст
  ответа провайдера, URL приватного репозитория): после правил секретов нет
- [ ] 6.3 Grafana: datasources Loki, Tempo, Prometheus с переходом лог ↔ трейс по `trace_id`.
  Проверка: локальный прогон — из записи лога с `trace_id` открывается трейс прогона в Tempo
- [ ] 6.4 `docker-compose.yml`: сервисы стека под профилем `observability`, экспорт включён
  окружением (`OTEL_TRACES_EXPORTER=otlp`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_RESOURCE_ATTRIBUTES`
  c `service.version` и `deployment.environment.name=local`), плагин `rabbitmq_prometheus` у
  локального RabbitMQ. Проверка: локальный прогон ревью виден в Grafana целиком — лог, трейс,
  метрики
- [ ] 6.5 `infra/` (Terraform): ресурсы пяти контейнеров стека в существующей сети с теми же
  конфигами из `infra/observability/`, `postgres-exporter`, переменные стека (ресурсы, хранение,
  доступ Grafana); `service.version` — sha образа в выводах сборки. Проверка: `tofu plan` без
  изменений вне стека; применение на стенд — по готовности DevOps

## 7. Документы и правила

- [ ] 7.1 `docs/BACKEND_ARCHITECTURE.md`: раздел наблюдаемости вместо «Prometheus и Grafana не
  нужны» — путь логов, стек, включение экспорта окружением, граница «телеметрия — для команды,
  данные продукта — из базы». Проверка: существующие тесты доков зелёные
- [ ] 7.2 `docs/testing/TEST_PLAN.md`: колонка метрик; тесты логов (поля, секреты, `500`),
  SSE-трейса, редакции коллектора. Проверка: описанные проверки соответствуют задачам 1–6
- [ ] 7.3 `.agents/rules/backend.md`, раздел «Наблюдаемость»: правила логов — только stdlib
  `logging.getLogger(__name__)`, поля JSON по спецификации, бизнес-контекст теми же именами, что в
  атрибутах спанов, контекст через `extra`/`Filter`, запретный список. Проверка: раздел читается
  как чек-лист для ревью логирующего кода

## 8. Финальные проверки

- [ ] 8.1 Полный набор: `uv run ruff check .`, `uv run lint-imports`, `uv run mypy .`,
  `uv run pytest`, `uv run python scripts/export_openapi.py --check`,
  `npx @fission-ai/openspec@1.14.2 validate --all --strict` — всё зелёное, пропущенных тестов нет
