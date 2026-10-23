# Proposal

## Why

Ревью PR #111 (задача #107): `GET /repositories/{id}` токеном пользователя открывает любой публичный репозиторий, с `permissions` только `pull`. `POST /repositories` с id чужого публичного репозитория отвечал `403 REPOSITORY_MANAGE_REQUIRED`, если приложение туда установлено, и `404`, если нет, — по ответу можно узнать, где стоит приложение. В списке `/user/installations…` такого репозитория нет, по спеке «не виден» — `404`.

## What Changes

- Репозиторий, у которого из прав только `pull` (push, triage, maintain и admin — `false`), при подключении считается невидимым: `404 REPOSITORY_NOT_IN_INSTALLATION` до проверки установки и прав, установка не запрашивается.
- `403 REPOSITORY_MANAGE_REQUIRED` остаётся для пользователя с push или triage без admin и maintain.

## Capabilities

### Modified Capabilities

- `review-rest-api`: «Отказы подключения репозитория».

## Impact

`app/domain/entities.py` (`RepositoryPermissions`: `push`, `triage`), `app/domain/repository_access.py` (`has_explicit_access`), `app/application/use_cases/connect_repository.py`, адаптер `app/infrastructure/vcs/github_installations.py` (переводит `push` и `triage`), описания ручек в `app/api/repositories.py`, `docs/openapi/openapi.json`, README, `docs/BACKEND_ARCHITECTURE.md`. Схема ответов не меняется.
