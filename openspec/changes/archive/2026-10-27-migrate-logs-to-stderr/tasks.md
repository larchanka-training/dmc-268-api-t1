# Tasks

## 1. Логи миграций — в stderr

- [x] 1.1 Тест: `configure_logging(..., stream=…)` пишет JSON в переданный поток, повторный вызов без `stream` поток не меняет (`tests/infrastructure/test_logging.py`)
- [x] 1.2 Адаптер `configure_logging` (`app/infrastructure/logging.py`) принимает `stream`
- [x] 1.3 `alembic/env.py` передаёт `sys.stderr` в оба вызова
- [x] 1.4 Тест на настоящем `alembic current`: stdout — только ревизия, JSON-логи — в stderr (`tests/db/test_alembic_env.py`)
- [x] 1.5 `migrate.sh`: комментарии про stdout/stderr, перенос строк около 104 и 118; фильтр в `current_revision` — страховка, докстринг обновлён

## 2. Документы и приёмка

- [x] 2.1 `docs/BACKEND_ARCHITECTURE.md`, `.agents/rules/backend.md`: логи миграций — в stderr
- [x] 2.2 ruff, lint-imports, mypy, pytest с `PYTEST_FAIL_ON_SKIP=1`, openspec validate
