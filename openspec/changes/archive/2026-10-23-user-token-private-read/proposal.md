# Proposal

## Why

После change `user-token-explicit-access` пользователь с ролью read получает при подключении `404 REPOSITORY_NOT_IN_INSTALLATION` и на публичном, и на приватном репозитории. На приватном это вводит в заблуждение: пользователь видит репозиторий в GitHub и в списке доступных, а ответ говорит, что приложения там нет, — он идёт просить установить уже установленное. Скрывать от него нечего: приватный репозиторий GitHub отдаёт только участнику.

## What Changes

- Приватный репозиторий с ролью read при подключении виден: если приложение установлено — `403 REPOSITORY_MANAGE_REQUIRED`, если нет — `404`, как у остальных.
- Публичный только с `pull` по-прежнему `404` до проверки установки: по ответу нельзя узнать, где стоит приложение.

## Capabilities

### Modified Capabilities

- `review-rest-api`: «Отказы подключения репозитория».

## Impact

`app/domain/entities.py` (`InstallationRepository.private`), `app/domain/repository_access.py` (`visible_for_connect`), `app/application/use_cases/connect_repository.py`, адаптер `app/infrastructure/vcs/github_installations.py` (переводит `private`), описания ручек в `app/api/repositories.py`, `docs/openapi/openapi.json`, README, `docs/BACKEND_ARCHITECTURE.md`. Схема ответов не меняется.
