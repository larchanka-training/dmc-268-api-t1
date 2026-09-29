## Why

Пайплайн (`add-rabbitmq-job-queue`, `add-review-pipeline`) уже кладёт
результат ревью в базу, но снаружи его прочитать нечем — у фронтенда нет ни
одного read-эндпоинта. Последняя часть DoD тикета («фронтенд может получить
результат по REST API») закрывается тремя чтениями поверх уже существующих
таблиц.

## What Changes

- `GET /api/v1/repositories` — постраничный список зарегистрированных
  репозиториев.
- `GET /api/v1/repositories/{repository_id}/pull-requests` — постраничный
  список запросов на изменение для репозитория; 404, если репозитория нет.
- `GET /api/v1/reviews/{review_run_id}` — прогон ревью с его находками; 404,
  если прогона нет.
- Расширение портов `RepositoryRepo`/`MergeRequestRepo` методами постраничного
  чтения (`list_all`, `list_for_repository`); `ReviewRunRepo.get` и
  `FindingRepo.list_for_run` уже существуют и используются как есть.
- **BREAKING**: нет.

## Capabilities

### New Capabilities
- `review-rest-api`: три read-эндпоинта для фронтенда — список репозиториев,
  список запросов на изменение репозитория, прогон ревью с находками;
  пагинация, коды ответов на отсутствующие ресурсы.

### Modified Capabilities
(нет — эндпоинты только читают то, что уже описывают `review-data-model` и
`backend-architecture`; ни один существующий контракт не меняется)

## Impact

- Новый код: `app/api/repositories.py`, `app/api/pull_requests.py`,
  `app/api/reviews.py`.
- Изменённый код: `app/api/factory.py` (подключение роутеров),
  `app/application/ports/repositories.py` (новые методы протоколов),
  `app/infrastructure/db/repositories.py` (их SQLAlchemy-реализация).
- Не затрагивает схему БД — миграций нет, новые методы читают существующие
  таблицы `repositories`, `merge_requests`, `review_runs`, `findings`.
- Без авторизации: в кодовой базе ещё нет JWT/`accounts` — это отдельный,
  не начатый шов (`docs/BACKEND_ARCHITECTURE.md`), эндпоинты его не
  изобретают заранее.
