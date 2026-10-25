## Why

`connect_repository` записывает `provider_id` из тела запроса, а не id из ответа GitHub. GitHub отвечает на `GET /repositories/0212613049` (и на `/+212613049`) тем же репозиторием, что на `/repositories/212613049`, поэтому один репозиторий подключается второй раз под другой записью id: проверка «уже подключён» его не видит, вебхук (`str(id)` из payload) и видимость (`str(id)` из списков) с такой записью не совпадают. Адаптер пропускает в путь запроса и не-ASCII цифры: `"²".isdigit()` — истина. Задача #154.

## What Changes

- Тело `POST /api/v1/repositories`: `provider_id` — по шаблону `^[1-9][0-9]*$`, иначе `422 VALIDATION_ERROR` до запроса к GitHub. Шаблон виден в схеме OpenAPI.
- Подключение записывает и проверяет на повтор id из ответа GitHub (`found.provider_id`), а не из запроса.
- `GitHubInstallationCatalog.user_repository` пропускает в путь только ASCII-цифры.

## Capabilities

### Modified Capabilities

- `review-rest-api`: новое требование «Подключение хранит канонический id репозитория».

## Impact

- Код: `app/api/repositories.py` (`ConnectRepositoryRequest.provider_id`), `app/application/use_cases/connect_repository.py`, `app/infrastructure/vcs/github_installations.py`, фейк `tests/fakes/installation_catalog.py` — ищет по числу, как GitHub.
- Контракт API: `pattern` у `provider_id` в теле подключения — `docs/openapi/openapi.json` перегенерируется.
- Данные: записи, подключённые раньше с неканоническим id, миграция не трогает; база стенда проверяется запросом из описания пул-реквеста.
