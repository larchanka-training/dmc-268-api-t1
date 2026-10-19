## 1. Сверка

- [x] 1.1 Сверить пути и методы `openapi.yaml` с роутами `app/api` и с `app.openapi()` FastAPI
- [x] 1.2 Сверить схемы ответов существующих ручек с pydantic-моделями: поля, `required`, `nullable`, коды ответов

## 2. Контракт

- [x] 2.1 Описать `GET /repositories` и `GET /repositories/{repositoryId}/pull-requests`
- [x] 2.2 Описать ответ `GET /reviews/{reviewId}` схемами `ReviewRun` и `ReviewRunFinding`, `404` и `422` — телами FastAPI
- [x] 2.3 Удалить `GET /reviews` и `GET /reviews/{reviewId}/findings`, пометить `POST /reviews` как не реализованный
- [x] 2.4 Дописать в вебхук `WebhookAcknowledged` и `422`, убрать `REVIEW_NOT_FOUND` из `ApiError.code`
- [x] 2.5 Синхронизировать §9 `docs/pipeline/PIPELINE_SPEC.md`

## 3. Проверки

- [x] 3.1 `uv run pytest` (в том числе `tests/test_docs.py`), `ruff`, `lint-imports`, `mypy`, `openspec validate --all --strict`
