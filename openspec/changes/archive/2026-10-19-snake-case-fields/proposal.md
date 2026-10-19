## Why

Решение 6 из dmc-268-ui-t1#37: в API один стиль полей. Сейчас `ReviewJob` и тела запросов — camelCase, а остальные ответы, событие SSE и колонки БД — snake_case; фронту генерируются оба стиля. Выбран snake_case — так уже отдаётся почти всё.

## What Changes

- **BREAKING** `ReviewJob` и `ReviewError` (тело `202` вебхука, ответ `POST /reviews` в схеме) — поля в snake_case: `provider_repository_id`, `pull_request_number`, `head_commit_sha`, `findings_count`, `last_progress_at` и т. д.
- **BREAKING** тела запросов `CreateReviewRequest` и `ConnectRepositoryRequest` — snake_case; ключ в camelCase — лишнее поле (`422`). Строгость прежняя: без лишних полей и приведения типов.
- Базы `CamelCaseResponse` / `CamelCaseRequest` заменены на `AllFieldsResponse` / `StrictRequest`. `operationId` остаётся camelCase — это имена операций, а не поля.

Ломается немного: `POST /reviews` и `POST /repositories` отвечают `501`, тело `202` вебхука GitHub не читает.

## Impact

- Код: `app/api/schemas.py`, `app/api/reviews.py`, `app/api/repositories.py`.
- Контракт: `docs/openapi/openapi.json` — переименования полей, состав не меняется.
- Тесты: `tests/test_problem_details.py`, `tests/api/test_webhooks.py`, `tests/api/test_webhooks_e2e.py`, `tests/api/test_contract_guards.py`.
- Документы: `.agents/rules/backend.md`, `.agents/templates/backend/endpoint.md`, `docs/pipeline/PIPELINE_SPEC.md`, `docs/pipeline/TEAM_ALIGNMENT_CHECKLIST.md`.
- Кабинет: тело подключения — `provider_id` (dmc-268-ui-t1#36).
