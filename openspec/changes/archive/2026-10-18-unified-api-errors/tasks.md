## 1. Реализация

- [x] 1.1 `app/api/errors.py`: исключение `ApiError` и обработчики его и `RequestValidationError`, подключены в `create_app`
- [x] 1.2 `404` ручек `reviews`, `review_events`, `pull_requests`, `auth` — `ApiError` с кодом по сущности вместо `HTTPException`

## 2. Контракт и документы

- [x] 2.1 `openapi.yaml`: `404` и `422` — `ApiError`, enum `ApiError.code` дополнен, `HTTPError` и `HTTPValidationError` удалены
- [x] 2.2 `PIPELINE_SPEC.md` §9 — список кодов, без примечания про `{"detail"}`
- [x] 2.3 Шаблон эндпоинта `.agents/templates/backend/endpoint.md` — отказ через `ApiError`

## 3. Тесты и проверки

- [x] 3.1 Тела `404` с кодами и `422` с `VALIDATION_ERROR` в тестах ручек
- [x] 3.2 `tests/test_docs.py` сверяет коды и в общих ответах `components.responses`
- [x] 3.3 `ruff`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict` проходят
