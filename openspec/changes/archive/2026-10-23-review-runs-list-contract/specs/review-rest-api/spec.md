## ADDED Requirements

### Requirement: Список прогонов описан заглушкой

`GET /api/v1/reviews` SHALL быть описан в схеме с полной моделью ответа и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован; сессию SHALL требовать. Ответ SHALL содержать `items` — строки `ReviewRunListItem` с `MergeRequestSummary` и числом находок — и `total` — число прогонов под фильтрами без учёта `limit` и `offset`.

#### Scenario: Список прогонов

- **WHEN** вызывается `GET /api/v1/reviews` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Список без сессии

- **WHEN** вызывается `GET /api/v1/reviews` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Контракт списка для фронтенда

- **WHEN** фронтенд читает `docs/openapi/openapi.json`
- **THEN** в `ReviewRunListResponse` есть обязательные `items` и `total`

### Requirement: Параметры списка прогонов проверяются раньше 501

`GET /api/v1/reviews` SHALL принимать `status` (несколько значений), `active`, `repository_id`, `author`, `q` (до 200 символов, пустая строка — без поиска), `created_from` (включительно), `created_to` (исключительно), `limit` и `offset` и SHALL проверять их раньше ответа `501`. `created_from` позже `created_to` SHALL отклоняться кодом `422`.

#### Scenario: Обратный диапазон дат

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from` позже `created_to`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `query.created_to`

#### Scenario: Статус не из списка

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и значением `status` вне `ReviewRunStatus`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR`

#### Scenario: Все фильтры сразу

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и допустимыми значениями всех фильтров
- **THEN** ответ — `501` с `code = NOT_IMPLEMENTED`

### Requirement: Поток изменений прогонов описан заглушкой

`GET /api/v1/reviews/events` SHALL быть описан в схеме как `text/event-stream` и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован; сессию SHALL требовать. Модели данных событий `run.created` (`ReviewRunListItem`), `run.status_changed` (`ReviewRunStatusChangedEvent`) и `run.finished` (`ReviewRunFinishedEvent`) SHALL быть в `components/schemas`. Путь SHALL NOT попадать в `GET /api/v1/reviews/{review_run_id}`.

#### Scenario: Поток изменений

- **WHEN** вызывается `GET /api/v1/reviews/events` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`, а не `422` проверки id прогона

#### Scenario: Поток без сессии

- **WHEN** вызывается `GET /api/v1/reviews/events` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Прогон по id

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` с id существующего прогона
- **THEN** ответ — `200` с этим прогоном, как до появления потока
