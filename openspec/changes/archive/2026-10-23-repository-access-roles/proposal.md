## Why

`GET /api/v1/repositories/available` отдавал репозитории всех установок GitHub App, в том числе имена приватных, любому вошедшему пользователю, а вход организацией не ограничен. `POST /api/v1/repositories` проверял «уже подключён» раньше прав, и пользователь без прав по `409` узнавал, подключён ли чужой репозиторий. Правка по ревью PR #96 (задача #41), поверх change `implement-repository-connect`.

## What Changes

- `GET /repositories/available` отдаёт только репозитории, к которым у вошедшего пользователя есть доступ в GitHub (любая роль); остальные в ответ не попадают, без ошибки. Схема ответа не меняется.
- `POST /repositories` подключает только при роли admin или maintain; права проверяются раньше «уже подключён»: `404` → `403` → `409` → `201`.
- Это уточнение решения 3A из larchanka-training/dmc-268-ui-t1#37 (там подключал только admin, а список не фильтровался): список — по доступу, подключение — admin или maintain.
- Порт `InstallationCatalog` отдаёт роль пользователя (`user_role`) вместо `is_admin`; тип `RepositoryRole` и решения «видит» и «подключает» — в домене.
- Код отказа `REPOSITORY_ADMIN_REQUIRED` переименован в `REPOSITORY_MANAGE_REQUIRED`: право теперь не только admin. Фронтенд контракт с этим кодом ещё не синхронизировал.
- Описания ручек, README, экспорт `docs/openapi/openapi.json`.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-rest-api`: список доступных репозиториев — по доступу пользователя; подключение — роль admin или maintain, порядок отказов, код `REPOSITORY_MANAGE_REQUIRED`.

## Impact

- `app/domain/enums.py` (`RepositoryRole`), `app/domain/repository_access.py`, `app/application/ports/installation_catalog.py`, `app/application/use_cases/connect_repository.py`, `app/infrastructure/vcs/github_installations.py`, `app/api/repositories.py`, `app/api/problems.py`, `app/api/errors.py`.
- `docs/openapi/openapi.json`, `README.md`, `docs/BACKEND_ARCHITECTURE.md`.
- Схемы тел запросов и ответов не меняются; в каталоге ошибок `REPOSITORY_ADMIN_REQUIRED` заменён на `REPOSITORY_MANAGE_REQUIRED`. Миграции нет.
