# Proposal

## Why

Зависимости в `uv.lock` отстают на патч или минорную версию: fastapi, alembic, uvicorn, starlette, psycopg, ruff, sqlalchemy и транзитивные. Обновлять их пачкой дешевле, чем разбираться с накопленным отставанием разом.

## What Changes

- `uv lock --upgrade` в пределах ограничений `pyproject.toml`.
- SQLAlchemy 2.0.52 → 2.1.1. Минорный релиз SQLAlchemy может менять поведение, поэтому проверен отдельно (см. примечания в `tasks.md`). `greenlet` уходит из зависимостей: в 2.1 он нужен только для asyncio, приложение синхронное.
- fastapi 0.142 приносит новую транзитивную зависимость `opentelemetry-api`.

**Не входит:** `grimp` и `import-linter` — их верхние границы в `pyproject.toml` намеренные (нет колёс для macOS arm64), uv их соблюдает.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: обновляются версии зависимостей, поведение сервиса не меняется — это и проверяет полный набор тестов.

## Impact

- `uv.lock`. `pyproject.toml` не меняется.
