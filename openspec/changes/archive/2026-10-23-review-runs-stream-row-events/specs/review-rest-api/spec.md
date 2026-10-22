## MODIFIED Requirements

### Requirement: Параметры списка прогонов проверяются раньше 501

`GET /api/v1/reviews` SHALL принимать `status` (несколько значений), `active`, `repository_id`, `author`, `q` (до 200 символов), `created_from` (включительно), `created_to` (исключительно), `limit` и `offset` и SHALL проверять их раньше ответа `501`. У `q` и `author` пустая строка SHALL означать «без фильтра». `created_from` и `created_to` SHALL принимать время с любой зоной. `created_from` позже `created_to` SHALL отклоняться кодом `422` вместе с остальными ошибками параметров.

#### Scenario: Обратный диапазон дат

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from` позже `created_to`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `query.created_to`

#### Scenario: Статус не из списка

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и значением `status` вне `ReviewRunStatus`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR`

#### Scenario: Все фильтры сразу

- **WHEN** вызывается `GET /api/v1/reviews` с сессией, допустимыми значениями всех фильтров, пустыми `q` и `author` и временем с зоной `+03:00`
- **THEN** ответ — `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Без сессии параметры не проверяются

- **WHEN** вызывается `GET /api/v1/reviews` без сессии и с неверными параметрами
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

### Requirement: Поток изменений прогонов описан заглушкой

`GET /api/v1/reviews/events` SHALL быть описан в схеме как `text/event-stream` и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован; сессию SHALL требовать. Данные событий `run.created`, `run.status_changed` и `run.finished` SHALL быть `ReviewRunListItem`, и схема SHALL связывать каждое событие с этой моделью. При переходе в итоговый статус SHALL приходить только `run.finished`. Путь SHALL NOT попадать в `GET /api/v1/reviews/{review_run_id}`.

#### Scenario: Поток изменений

- **WHEN** вызывается `GET /api/v1/reviews/events` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`, а не `422` проверки id прогона

#### Scenario: Поток без сессии

- **WHEN** вызывается `GET /api/v1/reviews/events` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Данные событий — строка списка

- **WHEN** фронтенд читает ответ потока в `docs/openapi/openapi.json`
- **THEN** `run.created`, `run.status_changed` и `run.finished` ссылаются на `ReviewRunListItem`

#### Scenario: Прогон по id

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` с id существующего прогона
- **THEN** ответ — `200` с этим прогоном, как до появления потока
