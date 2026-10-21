## 1. Провайдер и инструментирование

- [x] 1.1 Зависимости через `uv add`: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-instrumentation-httpx`, `opentelemetry-instrumentation-sqlalchemy`
- [x] 1.2 `app/infrastructure/telemetry.py`: `configure_tracing` (SDK-провайдер, пустой глобальный пропагатор, консольный экспорт по `OTEL_TRACES_EXPORTER=console`, httpx), `instrument_engine`, `current_trace_id`; вызовы в `create_app` и `run_worker`
- [x] 1.3 `TraceIdMiddleware` берёт trace id текущего спана, docstring о доверии к `traceparent`. Проверка: `tests/api/test_tracing.py`

## 2. Очередь и этапы

- [x] 2.1 `traceparent` в заголовках AMQP: спан публикации в `_publish`, `consuming_span` в consume-цикле воркера, `review_run_id` на спане обработки. Проверка: `tests/infrastructure/test_telemetry.py`
- [x] 2.2 Порт `StageTracer`, спаны в точках `StepRecorder`, адаптер `OtelStageTracer`; `opentelemetry` в запрещённых вендорах внутренних слоёв. Проверка: `tests/application/test_stage_tracing.py`, `uv run lint-imports`
- [x] 2.3 Сквозной трейс вебхук → очередь → этапы воркера на настоящих базе и брокере. Проверка: `tests/test_tracing_e2e.py`

## 3. Документация и проверки

- [x] 3.1 `docs/BACKEND_ARCHITECTURE.md` (наблюдаемость, порт `StageTracer`), `README.md`, `.env.example`
- [x] 3.2 `ruff check`, `lint-imports`, `pytest` с `TEST_DATABASE_URL` и `TEST_RABBITMQ_URL`, `alembic heads`, экспорт OpenAPI без изменений, `openspec validate --all --strict` проходят
