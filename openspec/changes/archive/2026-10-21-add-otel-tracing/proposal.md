## Why

`trace_id` сейчас есть только у HTTP-запроса в API (`secrets.token_hex(16)` в `TraceIdMiddleware`), а в воркер он не доходит: сообщение в RabbitMQ уходит без заголовков, и связать запрос вебхука с работой воркера можно только через `review_run_id`. Решение 5 из larchanka-training/dmc-268-ui-t1#37 — наблюдаемость через OpenTelemetry; эта задача — первая половина: SDK и сквозной trace id (larchanka-training/dmc-268-api-t1#84). Логи, хранилище трейсов и метрики — #89.

## What Changes

- SDK OpenTelemetry в API и воркере. HTTP-сервер — встроенная телеметрия FastAPI (берёт глобальный провайдер сама), httpx (GitHub, модель) и SQLAlchemy — пакеты `opentelemetry-instrumentation-*`.
- Контекст трассировки между API и воркером — W3C `traceparent` в заголовках AMQP; тело сообщения не меняется. Входящему `traceparent` от HTTP-клиентов не доверяем (запрос начинает новый трейс), заголовкам своей очереди — доверяем. Наш контекст не уходит в исходящие запросы к GitHub и провайдерам модели.
- Спаны этапов прогона (`fetching_diff`, `building_context`, `analysing`, `verifying`, `publishing`) с атрибутом `review_run_id` открываются и закрываются в тех же точках, что шаги `StepRecorder`; повтор этапа — отдельный спан. Слой приложения о OpenTelemetry не знает: порт `StageTracer`, адаптер — в инфраструктуре.
- `X-Trace-ID` и `trace_id` в теле ошибки — trace id спана запроса, формат прежний; для `500`, который отдаётся снаружи всех middleware, — тоже.
- Экспорт не настраивается: без настройки спаны никуда не уходят и ошибок экспорта в логе нет. `OTEL_TRACES_EXPORTER=console` печатает спаны в stdout для отладки.
- Граница записана явно: телеметрия — для команды, данные продукта для пользователя (длительность, токены, шаги) — из базы; ни один ответ API не строится из телеметрии.

## Capabilities

### New Capabilities

- `observability`: трассировка OpenTelemetry в API и воркере, спаны этапов, экспорт, граница телеметрии и данных продукта.

### Modified Capabilities

- `review-rest-api`: trace id ответа — trace id спана запроса; входящий `traceparent` не продолжает трейс.
- `job-queue`: контекст трассировки в заголовках сообщения.

## Impact

- Код: `app/infrastructure/telemetry.py` (новый), `app/application/ports/stage_tracer.py` (новый), `app/application/{review_run_steps,review_pipeline}.py`, `app/infrastructure/queue/rabbitmq.py`, `app/worker/factory.py`, `app/api/{trace_id,factory}.py`.
- Зависимости: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-sqlalchemy`.
- `pyproject.toml`: `opentelemetry` в запрещённых вендорах внутренних слоёв (`lint-imports`).
- Контракт API не меняется: заголовок и поле `trace_id` остаются прежними.
- Документация: `docs/BACKEND_ARCHITECTURE.md` (наблюдаемость, порты), `README.md`, `.env.example`.
