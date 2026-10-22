## 1. Заглушки

- [x] 1.1 `GET` и `PUT /api/v1/repositories/{repository_id}/settings` в `app/api/repositories.py`: модели `RepositorySettingsResponse`, `RepositorySettingsUpdate` (ограничения `BRANCH_PATTERN_MAX_LENGTH`, `BRANCH_FILTER_MAX_ITEMS`), ответ `501 NOT_IMPLEMENTED`
- [x] 1.2 `GET /api/v1/repositories/{repository_id}/rules` в `app/api/repositories.py`: `RulesStatusResponse` — объединение `RulesLoaded`, `RulesAbsent`, `RulesTooLarge`, `RulesUnavailable` по `status`, модели `RepoRule`, `RejectedRule`; ответ `501 NOT_IMPLEMENTED`
- [x] 1.3 Тесты: `501` на допустимый запрос, `401` без сессии, `403 ORIGIN_FORBIDDEN` для `PUT` без `Origin`, `422` на id и тело, схемы в экспортированном контракте

## 2. Контракт и проверки

- [x] 2.1 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)
- [x] 2.2 `ruff check`, `lint-imports`, `mypy`, `pytest`, `export_openapi.py --check`, `openspec validate --all --strict` проходят
