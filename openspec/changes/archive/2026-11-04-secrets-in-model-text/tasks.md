## 1. Редакция текста модели

- [x] 1.1 Тесты: секрет в `message`, `suggestion`, сводке и `reason` не выходит из `llm_schema`; правка в строке `+` подсказки остаётся (красные до правки)
- [x] 1.2 `redact_secrets` в `to_review_result` и `check_verdicts` (design D1, D2)
- [x] 1.3 Запрет цитировать значение секрета в `prompts/review/system.md`, пояснение метки в `prompts/review/verifier.md` (design D2, D3)

## 2. Завершение

- [x] 2.1 `ruff`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict`
- [x] 2.2 Синхронизация спеки и `openspec archive secrets-in-model-text`
