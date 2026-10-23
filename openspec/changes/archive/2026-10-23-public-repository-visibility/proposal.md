# Proposal

## Why

Вопрос на ревью PR #111 (задача #107): видимость публичных репозиториев сузилась, но это нигде не названо. Change `repository-visibility-private` открывал публичный репозиторий всем; с `repositories-via-user-token` видно только то, что GitHub отдаёт по `/user/installations…`, — репозитории с явной ролью пользователя. Сторонний пользователь не видит публичный репозиторий в `GET /repositories/available` и не получает событий его прогонов в `GET /events`. Это и есть правило dmc-268-ui-t1#43 («доступ на GitHub, любая роль»), но его следствие для публичных репозиториев стоит записать.

## What Changes

- Сценарии: публичный репозиторий без явного права не виден в `GET /repositories/available` и в потоке `GET /events`.
- Описания ручек называют это следствие. Поведение кода не меняется.

## Capabilities

### Modified Capabilities

- `review-rest-api`: «Список репозиториев, доступных для подключения», «Типы и видимость событий общего потока описаны».

## Impact

Описания в `app/api/reviews.py` (`VISIBILITY_RULE`) и `app/api/repositories.py`, `docs/openapi/openapi.json`. Схема ответов не меняется.
