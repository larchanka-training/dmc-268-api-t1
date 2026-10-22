## Why

Экран настроек репозитория во фронтенде (dmc-268-ui-t1#28, PR dmc-268-ui-t1#29) ходит в ручки, которых нет в контракте: настройки ревью репозитория и статус `.review/rules.md`. Порядок задачи #113 — «контракт первым»: ручки заводятся заглушками `501 NOT_IMPLEMENTED` с полными схемами, фронтенд берёт типы через `pnpm api:sync`, реализация — отдельно.

## What Changes

- `GET /api/v1/repositories/{repository_id}/settings` — заглушка чтения настроек (`RepositorySettingsResponse`: `auto_review_enabled`, `branch_filter`, `min_severity` из `FindingSeverity`). Значения по умолчанию для репозитория без сохранённых настроек — в описании: `true`, `[]`, `critical`.
- `PUT /api/v1/repositories/{repository_id}/settings` — заглушка записи, тело `RepositorySettingsUpdate`: те же три поля, все обязательные, полная замена, лишние поля запрещены; паттерн ветки непустой, не длиннее 200 символов, не больше 50 паттернов. В схеме — `403` `ORIGIN_FORBIDDEN` и `REPOSITORY_MANAGE_REQUIRED`; роль заглушка не проверяет.
- `GET /api/v1/repositories/{repository_id}/rules` — заглушка статуса правил (`RulesStatusResponse`, дискриминатор `status` по `RulesStatus`: `RulesLoaded`, `RulesAbsent`, `RulesTooLarge`, `RulesUnavailable`).
- Все три требуют сессию, `PUT` — ещё `Origin`; id репозитория и тело проверяются раньше `501`.

Вне этого change: хранение настроек, их применение в конвейере, проверка роли у GitHub, чтение `.review/rules.md` с ветки по умолчанию. История проверок — `GET /api/v1/reviews?repository_id=…` (dmc-268-ui-t1#43).

## Impact

- Код: `app/api/repositories.py`.
- Контракт: `docs/openapi/openapi.json` — три операции, новые схемы; существующие не меняются.
- Тесты: `tests/test_problem_details.py`.
