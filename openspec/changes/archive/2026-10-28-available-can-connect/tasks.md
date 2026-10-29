# Tasks

## 1. Поле can_connect

- [x] 1.1 Красные тесты: `can_connect` в `list_available_repositories` (связка; роли держит доменный `test_only_admin_or_maintain_connects`); в API — совпадает с исходом `POST /repositories` (201/409 против 403/404) для того же пользователя и репозитория
- [x] 1.2 `AvailableRepository.can_connect` из `can_connect(permissions)`; `AvailableRepositoryResponse.can_connect`
- [x] 1.3 `uv run python scripts/export_openapi.py`

## 2. Приёмка

- [x] 2.1 ruff, lint-imports, mypy, pytest с `PYTEST_FAIL_ON_SKIP=1`, openspec validate
