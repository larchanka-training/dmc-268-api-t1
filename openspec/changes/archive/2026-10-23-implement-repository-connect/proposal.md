## Why

Спека `webhook-intake` запрещает регистрировать репозиторий по вебхуку: регистрацию выполняет отдельная операция с авторизацией. Её форма зафиксирована заглушками `501` (`GET /api/v1/repositories/available`, `POST /api/v1/repositories`), а репозитории до сих пор вносятся в базу вручную. Экран «Подключить репозиторий» в кабинете уже ходит в эти ручки и заработает без правок, когда они ответят по контракту (задача #41, решение 3A в dmc-268-ui-t1#37).

## What Changes

- `GET /api/v1/repositories/available` отдаёт репозитории всех установок GitHub App с отметкой `already_connected`.
- `POST /api/v1/repositories` подключает репозиторий установки: проверяет у GitHub права admin вошедшего пользователя и записывает репозиторий с `auto_review_enabled = true`.
- Новые коды ошибок: `404 REPOSITORY_NOT_IN_INSTALLATION`, `409 REPOSITORY_ALREADY_CONNECTED`, `403 REPOSITORY_ADMIN_REQUIRED`; сбой GitHub — `502 SCM_UNAVAILABLE`.
- Новый порт `InstallationCatalog` и его адаптер GitHub: репозитории установок по JWT приложения и права пользователя на репозиторий.
- `installation_id` не хранится: его даёт список установок (решение общее для #41 и #87, см. design).
- Схемы запроса и ответа ручек не меняются, меняются `description` и `responses`.
- README: «Регистрация репозитория» — через кабинет; ручная вставка — для разработки.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-rest-api`: подключение репозитория реализовано вместо заглушек `501`.

## Impact

- `app/api/repositories.py`, `app/api/problems.py`, `app/api/errors.py`, `app/api/dependencies.py`.
- `app/application/ports/installation_catalog.py`, `app/application/use_cases/connect_repository.py`, `app/domain/entities.py`.
- `app/infrastructure/vcs/github_installations.py`, `app/infrastructure/vcs/github_auth.py` (публичный JWT приложения), `app/infrastructure/container.py`.
- `RepositoryRepo.add` возвращает `False`, если пара `(provider, provider_id)` уже занята (`ON CONFLICT DO NOTHING`), — гонка двух подключений даёт `409`, а не `500`.
- `docs/openapi/openapi.json` — экспорт. Миграции нет.
