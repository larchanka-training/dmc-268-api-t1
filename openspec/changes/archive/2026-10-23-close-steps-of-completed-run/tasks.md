## 1. Реализация

- [x] 1.1 `run_review` вызывает `close_abandoned_steps` с `StepRecorder.unwritten` после перевода прогона в `completed`. Проверка: `tests/application/test_review_pipeline.py`

## 2. Проверки

- [x] 2.1 `ruff check`, `lint-imports`, `mypy`, `pytest`, `export_openapi.py --check`, `openspec validate --all --strict` проходят
