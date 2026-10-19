# Proposal

## Why

pydantic, fastapi и starlette отстают на минорную версию. Контракт API строится из кода (PR #70), поэтому на свежих версиях держим и схему: обновление показывает, меняет ли новая версия экспорт `openapi.json`.

## What Changes

- `uv lock --upgrade-package` для четырёх пакетов: pydantic 2.13.5 → 2.14.1, pydantic-core 2.46.5 → 2.50.1, fastapi 0.142.2 → 0.146.0, starlette 1.7.0 → 1.9.0. Остальные версии в `uv.lock` не меняются.
- `pydantic>=2.13` — явная зависимость: код API опирается на `validate_by_name` (2.11) и на то, что pydantic сам отдаёт время UTC с `Z` (2.13); раньше pydantic приходил только транзитивно через fastapi.
- fastapi 0.143: автонастройка экспортёров OpenTelemetry из `OTEL_*` теперь включается явно. Приложение её не использует.
- fastapi 0.145–0.146: удалён `openapi_prefix`, `root_path` в `FastAPI()` устарел. В коде нет ни того, ни другого.
- starlette 1.9: `TestClient` отдаёт тело потоком; тесты SSE это не задело.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

Нет.

`skip_specs: true`: обновляются версии зависимостей, поведение сервиса не меняется — это проверяет полный набор тестов.

## Impact

- `uv.lock`, `pyproject.toml` (`pydantic>=2.13`).
