## 1. Контракт

- [x] 1.1 `repository_id` в `ReviewRunListQuery` — список UUID, описание ручки обновлено, `docs/openapi/openapi.json` перегенерирован. Проверка: `tests/test_problem_details.py`, `export_openapi.py --check`

## 2. Проверки

- [x] 2.1 `ruff check`, `mypy`, `lint-imports`, `pytest`, `openspec validate --all --strict` проходят
