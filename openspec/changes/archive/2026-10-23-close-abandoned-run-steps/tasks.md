## 1. Домен

- [x] 1.1 `app/domain/run_steps.py`: чистая `abandon_steps` — шаги `running` в `failed` с `REVIEW_RUN_STALE`. Проверка: `tests/domain/test_run_steps.py`

## 2. Приложение

- [x] 2.1 `close_abandoned_steps` в `app/application/review_run_steps.py`; вызов после перевода прогона в `failed` в `_mark_failed` и `sweep_stale_runs`. Проверка: `tests/application/test_review_pipeline.py`, `tests/application/test_stale_sweep.py`, `tests/db/test_adapters.py` на настоящей базе
- [x] 2.2 `run_review`: шаг открывается до записи прогона, которую он покрывает; не записанные в прогон токены — в `_mark_failed`. Проверка: `tests/application/test_review_pipeline.py`

## 3. Проверки

- [x] 3.1 `ruff check`, `lint-imports`, `mypy`, `pytest` с `TEST_DATABASE_URL`, `export_openapi.py --check`, `openspec validate --all --strict` проходят
