# Proposal

## Why

На странице подключения кабинет показывает кнопку «Подключить» у каждого неподключённого репозитория из `GET /repositories/available`. В список попадают репозитории с любой ролью пользователя, а подключить можно только с admin или maintain: остальным кнопка даёт `403 REPOSITORY_MANAGE_REQUIRED` уже после нажатия. Признака «можно подключить» в ответе нет — фронтенду не из чего это понять. Задача larchanka-training/dmc-268-ui-t1#45, бэкенд — #106.

## What Changes

- В строке `GET /api/v1/repositories/available` — `can_connect: bool`: у пользователя роль admin или maintain. Правило то же, что проверяет `POST /repositories` (`can_connect` в `app/domain/repository_access.py`), права — из того же ответа GitHub, что и список: новых запросов к GitHub нет.
- `POST /repositories` проверяет права сам, как раньше: поле — подсказка интерфейсу, а не защита.
- `docs/openapi/openapi.json` перегенерирован.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `review-rest-api`: ADDED «Признак права на подключение в списке доступных».

## Impact

- `app/application/use_cases/connect_repository.py` (`AvailableRepository.can_connect`), `app/api/repositories.py` (`AvailableRepositoryResponse.can_connect`), тесты, `docs/openapi/openapi.json`.
- Контракт: новое обязательное поле в ответе; фронтенд берёт его через `pnpm api:sync` (ui#46).
