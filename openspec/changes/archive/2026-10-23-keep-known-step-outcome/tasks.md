## 1. Реализация

- [x] 1.1 `abandon_steps` принимает известные исходы (`known`), `StepRecorder.unwritten` их копит, `_mark_failed` передаёт в `close_abandoned_steps`. Проверка: `tests/domain/test_run_steps.py`, `tests/application/test_review_pipeline.py`

## 2. Проверки

- [x] 2.1 `ruff check`, `lint-imports`, `mypy`, `pytest`, `export_openapi.py --check`, `openspec validate --all --strict` проходят
