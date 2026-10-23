## 1. Контракт и код

- [x] 1.1 `GET /api/v1/repositories/{repository_id}` в `app/api/repositories.py`. Проверка: `tests/test_repositories_api.py`
- [x] 1.2 Описание `min_severity` по решению #28, `docs/openapi/openapi.json` перегенерирован. Проверка: `export_openapi.py --check`

## 2. Проверки

- [x] 2.1 `ruff check`, `mypy`, `lint-imports`, `pytest`, `openspec validate --all --strict` проходят
