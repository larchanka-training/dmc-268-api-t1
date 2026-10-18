## 1. Проверки запроса

- [x] 1.1 `app/api/origin.py`: `OriginMiddleware` (чистый ASGI) и `origin_required`; `require_origin` удалена из `app/api/session.py`, `create_app` ставит middleware внутри `TraceIdMiddleware`
- [x] 1.2 `app/api/errors.py`: `validation_errors` без ключа лишнего поля, причины для длины, границ и `int_parsing_size`
- [x] 1.3 `app/api/pagination.py`: `Limit`, `Offset` (`le` = максимум bigint), `page_size`
- [x] 1.4 `app/api/schemas.py`: `CamelCaseRequest` (strict, только camelCase, без лишних полей) и `CreateReviewRequest` с границами колонок

## 2. Ответы

- [x] 2.1 `app/api/schemas.py`: `UtcDatetime`, `FailedStage`, `CamelCaseResponse` с обязательными в схеме полями
- [x] 2.2 Модели ответов через `UtcDatetime`, `FailedStage`, `UUID`, `PositiveInt`; `model_validate(..., from_attributes=True)` где поля совпадают
- [x] 2.3 `app/api/webhooks.py`: ответы — модели, без `JSONResponse`
- [x] 2.4 `app/api/review_events.py`: `format_sse` через модель `ReviewRunStatusEvent` (из `app/domain/run_events.py`)
- [x] 2.5 `app/api/openapi.py`: обёртка штатного `app.openapi`; описания полей и операций; `HTTPStatus` вместо чисел
- [x] 2.6 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)

## 3. Тесты, правила, проверки

- [x] 3.1 Тесты: `Origin` раньше тела, эхо ключа, `offset`, строгое тело, стражи формата (`tests/api/test_contract_guards.py`), тред вебхука, `export_openapi.py --check`
- [x] 3.2 `.agents/rules/backend.md`, шаблон эндпоинта, `AGENTS.md`, `docs/BACKEND_ARCHITECTURE.md`
- [x] 3.3 `ruff`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict`, `redocly lint` проходят
