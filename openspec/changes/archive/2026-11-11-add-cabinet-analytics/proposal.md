## Why

#281, #282. Дашборду кабинета (ui#109) нужны замечания по категориям во времени и средняя
длительность прогона по репозиторию, а на бэкенде агрегатов нет: фронтенд мог бы считать их только
перебором всех прогонов через `GET /reviews` и `GET /reviews/{id}`.

## What Changes

- Новая ручка `GET /api/v1/analytics` под сессией: за период замечания по UTC-дням, категориям и
  важности, число, средняя и p95 длительность прогонов — всего и по каждому репозиторию. Замечания —
  только новые: повтор открытого или закрытого человеком треда бота не считается.
- Видимость — правило `GET /reviews`: только репозитории, которые пользователь видит на GitHub.
  `repository_id` сужает всё до одного репозитория; невидимый или несуществующий — `404
  REPOSITORY_NOT_FOUND`.
- Период `created_from`/`created_to` по `created_at` прогона, по умолчанию последние 30 дней, не длиннее 180
  дней; ошибки периода — `422 VALIDATION_ERROR` раньше обращения к GitHub.
- Агрегаты считает база (`GROUP BY` по дню, `avg`, `percentile_cont`), новым методом порта
  `ReviewRunRepo.analytics`. Миграции нет: хватает индексов `(created_at, id)` прогонов и
  `review_run_id` находок.

## Capabilities

### New Capabilities

### Modified Capabilities

- `review-rest-api`: ручка аналитики кабинета.

## Impact

- `app/domain/analytics.py`, `app/api/analytics.py`, `app/api/factory.py`,
  `app/application/ports/repositories.py`, `app/infrastructure/db/repositories.py`.
- Тесты: `tests/test_analytics_api.py`, `tests/db/test_analytics_adapter.py`, фейк
  `tests/fakes/unit_of_work.py`.
- `docs/openapi/openapi.json` — новая операция и модели; фронтенд генерирует по ним типы.
