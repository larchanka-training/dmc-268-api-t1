## 1. Тесты

- [x] 1.1 Красный тест API: `provider_id` `"0101"`, `"١٠١"`, `"²"` — `422` с `where = body.provider_id`, к GitHub запросов нет (`tests/test_repositories_api.py`)
- [x] 1.2 Фейк каталога ищет репозиторий по числу, как GitHub (`tests/fakes/installation_catalog.py`)
- [x] 1.3 Красные тесты use case: подключение `"0101"` записывает `"101"`; при подключённом `"101"` — `ALREADY_CONNECTED` (`tests/application/test_connect_repository.py`)
- [x] 1.4 Красный тест адаптера: не-ASCII цифры — `None` без запроса (`tests/infrastructure/test_github_installations.py`)

## 2. Код

- [x] 2.1 `ConnectRepositoryRequest.provider_id` — `pattern=r"^[1-9][0-9]*$"`
- [x] 2.2 `connect_repository` записывает и проверяет `found.provider_id`
- [x] 2.3 `GitHubInstallationCatalog.user_repository` — только ASCII-цифры
- [x] 2.4 `uv run python scripts/export_openapi.py`
