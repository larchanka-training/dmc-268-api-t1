## 1. Код

- [x] 1.1 `AllFieldsResponse` и `StrictRequest` вместо camelCase-баз; `ReviewJob`, `ReviewError`, тела запросов — snake_case
- [x] 1.2 Тесты: snake_case в телах и ответах, camelCase-ключ — `422`

## 2. Контракт и документы

- [x] 2.1 Экспорт `docs/openapi/openapi.json`
- [x] 2.2 Правила, шаблон ручки, PIPELINE_SPEC, чек-лист — snake_case
- [x] 2.3 `ruff check`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict` проходят
