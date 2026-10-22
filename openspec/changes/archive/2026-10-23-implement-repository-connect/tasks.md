## 1. Ядро

- [x] 1.1 Доменный тип `InstallationRepository` в `app/domain/entities.py`
- [x] 1.2 Порт `InstallationCatalog` в `app/application/ports/installation_catalog.py`
- [x] 1.3 Use cases `list_available_repositories` и `connect_repository` в `app/application/use_cases/connect_repository.py`, тесты на фейках: успех, `404`, `409`, `403`, гонка при `add`
- [x] 1.4 `RepositoryRepo.add` возвращает `False` на занятую пару `(provider, provider_id)` (адаптер — `ON CONFLICT DO NOTHING`), фейк — так же

## 2. Адаптер

- [x] 2.1 `GitHubAppAuth.app_jwt()` — публичный JWT приложения
- [x] 2.2 `GitHubInstallationCatalog` в `app/infrastructure/vcs/github_installations.py`, тесты на `httpx.MockTransport`: пагинация установок и репозиториев, права admin/write/404, сбой — `VcsError`
- [x] 2.3 Ленивая сборка в `Container.installation_catalog()`, зависимость `get_installation_catalog`

## 3. HTTP

- [x] 3.1 Коды `REPOSITORY_NOT_IN_INSTALLATION`, `REPOSITORY_ALREADY_CONNECTED`, `REPOSITORY_ADMIN_REQUIRED` с моделями и `ERRORS_*`; `403` из двух моделей у `POST /repositories`, сверка в `tests/test_docs.py`
- [x] 3.2 Ручки вместо заглушек, описания; API-тесты: список с `already_connected`, подключение `201/403/404/409/502`, без сессии `401`
- [x] 3.3 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`)

## 4. Документы и проверки

- [x] 4.1 README: регистрация через кабинет, ручная — для разработки; разрешение Metadata: read
- [x] 4.2 `ruff check`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict`, `alembic heads` проходят
