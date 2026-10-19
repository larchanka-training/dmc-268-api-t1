## 1. Тест

- [x] 1.1 Сверить множество (метод, путь) под `/api/v1` из `app.openapi()` с `openapi.yaml`, с учётом `x-not-implemented`
- [x] 1.2 Сверить тела 2xx `application/json` общих ручек: поля, `required`, nullable — рекурсивно
- [x] 1.3 Пометить `POST /reviews` как `x-not-implemented: true`
- [x] 1.4 Убедиться, что тест падает на удалённом пути, поле, `required`, nullable и на лишней или снятой пометке

## 2. Правило

- [x] 2.1 Строка в `AGENTS.md`, раздел «Правила, которые нельзя нарушать»
- [x] 2.2 Раздел «HTTP API и контракт» в `.agents/rules/backend.md`

## 3. Проверки

- [x] 3.1 `uv run pytest`, `ruff`, `lint-imports`, `mypy`, `openspec validate --all --strict`
