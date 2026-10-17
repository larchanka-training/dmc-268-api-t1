# Tasks

## 1. Логирование миграций

- [x] 1.1 `alembic/env.py`: `fileConfig(config.config_file_name, disable_existing_loggers=False)` с комментарием, почему.
      Проверка: с `TEST_DATABASE_URL` и `TEST_RABBITMQ_URL` полный `uv run pytest` - проверки лога из `tests/test_container.py`, `tests/infrastructure/test_fallback_gateway.py` и `tests/infrastructure/test_openai_compatible_gateway.py` проходят после интеграционных тестов миграций (до исправления три из них падали).
- [x] 1.2 `tests/worker/test_review_handler.py`: убрать обход через `monkeypatch.setattr(..., "disabled", False)`.
      Проверка: `test_handler_logs_both_ids_when_the_run_fails` проходит в полном интеграционном прогоне без обхода; два прогона подряд - 775 passed без пропусков.
