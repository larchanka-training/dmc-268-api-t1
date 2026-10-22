## MODIFIED Requirements

### Requirement: Параметры списка прогонов проверяются раньше 501

`GET /api/v1/reviews` SHALL проверять параметры фильтров и пагинации раньше ответа `501`. У `q` и `author` пустая строка SHALL означать «без фильтра». `created_from` и `created_to` SHALL принимать только строку ISO 8601 с зоной, любой. `created_from` позже `created_to` SHALL отклоняться кодом `422` вместе с остальными ошибками параметров.

#### Scenario: Обратный диапазон дат

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from` позже `created_to`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `query.created_to`

#### Scenario: Время без зоны или не ISO 8601

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from` без зоны или `created_to` в виде unix-времени
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и причиной «ожидается время с зоной» или «ожидается время в ISO 8601»

#### Scenario: Статус не из списка

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и значением `status` вне `ReviewRunStatus`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR`

#### Scenario: Все фильтры сразу

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и допустимыми `status` (несколько значений), `active`, `repository_id`, `created_from`, `created_to`, `limit`, `offset`, пустыми `q` и `author` и временем с зоной `+03:00`
- **THEN** ответ — `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Без сессии параметры не проверяются

- **WHEN** вызывается `GET /api/v1/reviews` без сессии и с неверными параметрами
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

### Requirement: Поток изменений прогонов описан заглушкой

`GET /api/v1/reviews/events` SHALL быть описан в схеме как `text/event-stream` и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован; сессию SHALL требовать. Данные событий `run.*` SHALL быть `ReviewRunListItem`, имена событий — enum `ReviewRunsStreamEventType`. При итоговом статусе SHALL приходить только `run.finished`. Путь SHALL NOT попадать в `GET /api/v1/reviews/{review_run_id}`.

#### Scenario: Поток изменений

- **WHEN** вызывается `GET /api/v1/reviews/events` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`, а не `422` проверки id прогона

#### Scenario: Поток без сессии

- **WHEN** вызывается `GET /api/v1/reviews/events` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Данные событий — строка списка

- **WHEN** фронтенд читает ответ потока и `components/schemas` в `docs/openapi/openapi.json`
- **THEN** `run.created`, `run.status_changed` и `run.finished` ссылаются на `ReviewRunListItem`, а `ReviewRunsStreamEventType` перечисляет `run.created`, `run.status_changed`, `run.finished` и `ping`

#### Scenario: Прогон по id

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` с id существующего прогона
- **THEN** ответ — `200` с этим прогоном, как до появления потока

## ADDED Requirements

### Requirement: Порядок версий строки в потоке прогонов описан

Описание `GET /api/v1/reviews/events` SHALL задавать правило для клиента: upsert строки по `id`; побеждает больший `updated_at`, при равном — версия, пришедшая позже; снимок `GET /api/v1/reviews` запрашивается после события `open`. Поток SHALL NOT учитывать фильтры списка: при активных фильтрах или не на первой странице клиент на событие перезапрашивает страницу.

#### Scenario: Правило в контракте

- **WHEN** фронтенд читает описание `GET /api/v1/reviews/events` в `docs/openapi/openapi.json`
- **THEN** в нём есть правило выбора версии по `updated_at`, порядок «`open`, затем снимок» и перезапрос страницы при фильтрах
