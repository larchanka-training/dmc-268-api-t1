# Tasks

## 1. Обновление

- [x] 1.1 `uv lock --upgrade`; `grimp` и `import-linter` остаются в своих границах

## 2. Приёмка

- [x] 2.1 Локально: ruff, lint-imports, mypy, полный pytest с PostgreSQL без пропусков
- [x] 2.2 SQLAlchemy 2.1: тесты с `-W error::DeprecationWarning`, предупреждений SQLAlchemy нет
- [x] 2.3 Зелёный CI

## Примечания к выполнению

2.1–2.2: 127 passed, 0 skipped против `postgres:18-alpine`, включая миграции и тест расхождения схемы с моделями; с `-W error::DeprecationWarning` — тоже 127 passed. Предупреждений в прогоне столько же, сколько на SQLAlchemy 2.0.54 (10), ни одно не от SQLAlchemy.

2.3: [CI 36789961419](https://github.com/larchanka-training/dmc-268-api-t1/actions/runs/36789961419) зелёный, 127 passed.
