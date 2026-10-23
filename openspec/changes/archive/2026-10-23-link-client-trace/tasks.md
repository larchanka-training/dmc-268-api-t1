## 1. Ссылка на контекст клиента

- [x] 1.1 `link_client_trace` в `app/infrastructure/telemetry.py`: разбор `traceparent` пропагатором W3C, ссылка на текущий спан с `link.source = "client"`
- [x] 1.2 Вызов в `TraceIdMiddleware` (`app/api/trace_id.py`), где серверный спан запроса уже текущий
- [x] 1.3 Тесты в `tests/api/test_tracing.py`: валидный заголовок — корень с одной ссылкой; флаг `00` — запись как без заголовка; невалидный — без ссылки; `X-Trace-ID` — trace id сервера; сэмплер `parentbased_*` не зависит от заголовка

## 2. Документация

- [x] 2.1 Раздел «Наблюдаемость» в `docs/BACKEND_ARCHITECTURE.md`, правило доверия в `.agents/rules/backend.md`

## 3. Проверки

- [x] 3.1 `ruff check`, `mypy`, `lint-imports`, `export_openapi.py --check`, `pytest`, `openspec validate --all --strict` проходят
