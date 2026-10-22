## 1. Ядро

- [x] 1.1 `RepositoryRole` в `app/domain/enums.py`; чистые `can_see` и `can_connect` в `app/domain/repository_access.py` с тестами
- [x] 1.2 Порт `InstallationCatalog.user_role` вместо `is_admin`; фейк отдаёт роли из литералов
- [x] 1.3 `list_available_repositories` принимает `login` и оставляет репозитории с доступом; тесты: без доступа не показывается, read показывается, пустой список, сбой — `VcsError`
- [x] 1.4 `connect_repository`: роль admin или maintain, права раньше «уже подключён»; тесты: maintain подключает, ниже maintain — отказ, без прав на подключённый — отказ по правам

## 2. Адаптер

- [x] 2.1 `GitHubInstallationCatalog.user_role`: `role_name`, без него — `permission`, `none` и `404` — нет доступа, незнакомое имя — `CUSTOM`; тесты на оба поля

## 3. HTTP и документы

- [x] 3.1 Код `REPOSITORY_ADMIN_REQUIRED` → `REPOSITORY_MANAGE_REQUIRED`; ручка списка передаёт `login`; описания ручек; API-тесты: read видит и получает `403`, maintain подключает, `502` при сбое запроса роли, `403` вместо `409`
- [x] 3.2 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)
- [x] 3.3 README и `docs/BACKEND_ARCHITECTURE.md`
- [x] 3.4 `ruff check`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict`, `alembic heads` проходят
