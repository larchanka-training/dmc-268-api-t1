## Why

Задача #68. Ручки `/api/v1` отвечали ошибками в двух форматах: вебхуки и проверка сессии — `ApiError {code, message}`, а `404` ручек чтения и входа и `422` проверки параметров — телами FastAPI `{"detail": …}`. Фронту приходилось разбирать оба, а `PIPELINE_SPEC.md` требует `ApiError` для всех ошибок HTTP-слоя.

## What Changes

- Все ошибки ручек `/api/v1` — `ApiError`. `404` получает код по сущности: `REVIEW_NOT_FOUND` (прогон, в том числе поток событий), `REPOSITORY_NOT_FOUND`; вход через неподдержанного провайдера — `404` с уже существующим `PROVIDER_NOT_SUPPORTED`.
- Ошибка проверки параметров пути и запроса — `422` с `VALIDATION_ERROR`; в `message` — где ошибка и почему (`query.limit: ожидается целое число`), без самого значения.
- `openapi.yaml`: ответы `404` и `422` — `ApiError`, enum `ApiError.code` дополнен, схемы `HTTPError` и `HTTPValidationError` удалены. `PIPELINE_SPEC.md` §9 — без примечания про `{"detail"}`.
- `401 UNAUTHENTICATED`, `403 ORIGIN_FORBIDDEN` и ответы вебхуков не меняются.

## Impact

- Код: `app/api/errors.py` (исключение `ApiError` и обработчики), `app/api/factory.py`, ручки `reviews`, `review_events`, `pull_requests`, `auth`.
- Контракт: `docs/openapi/openapi.yaml`, `docs/pipeline/PIPELINE_SPEC.md`; фронт, читавший `detail`, должен читать `code`.
- Тесты: тела `404` и `422` в `tests/test_*_api.py`; `tests/test_docs.py` учитывает общие ответы из `components.responses`.
