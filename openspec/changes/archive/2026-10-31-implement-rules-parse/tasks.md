## 1. Разбор с позициями

- [x] 1.1 Чистая `outline_rules_markdown` в `app/domain/repo_rules.py`: `RuleOutline` (правило, строка заголовка, строки пунктов) и `RejectedOutline` (раздел, строка заголовка), в том числе у разделов внутри незакрытого блока кода; `parse_rules_markdown` - её проекция без позиций
- [x] 1.2 Чистая `matching_files` в `app/domain/repo_rules.py`; `applicable_rules` отбирает файлы ею же
- [x] 1.3 Тесты без базы: строки заголовков и пунктов, отклонённые разделы, незакрытый блок, `\r\n` и `\x0c`, равенство разбора ревью и контура без позиций, порядок `matching_files`

## 2. Ручка

- [x] 2.1 `POST /api/v1/rules/parse` в `app/api/playground.py` отвечает `200` с `RulesParseResponse`; `501` снят из схемы операции
- [x] 2.2 Лимит `RULES_MAX_BYTES` в байтах UTF-8 - `422` в `body.rules`; `ApiError` несёт `errors` до `problem_response`
- [x] 2.3 Тесты ручки в `tests/test_playground_api.py`: ответ на текст с правилами и отклонённым разделом, без `paths`, пустой текст, лимит в байтах; `tests/test_problem_details.py` - без `501` у разбора

## 3. Контракт и проверки

- [x] 3.1 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)
- [x] 3.2 `ruff check`, `lint-imports`, `mypy`, `pytest`, `export_openapi.py --check`, `openspec validate --all --strict` проходят
