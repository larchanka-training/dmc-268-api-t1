## MODIFIED Requirements

### Requirement: Параметры списка прогонов проверяются раньше 501

`GET /api/v1/reviews` SHALL проверять параметры фильтров и пагинации раньше ответа `501`. `status` и `repository_id` SHALL принимать несколько значений, параметр повторяется. У `q` и `author` пустая строка SHALL означать «без фильтра». `created_from` и `created_to` SHALL принимать только строку ISO 8601 с зоной, любой. `created_from` позже `created_to` SHALL отклоняться кодом `422` вместе с остальными ошибками параметров.

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

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и допустимыми `status` (несколько значений), `active`, `repository_id` (несколько значений), `created_from`, `created_to`, `limit`, `offset`, пустыми `q` и `author` и временем с зоной `+03:00`
- **THEN** ответ — `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Без сессии параметры не проверяются

- **WHEN** вызывается `GET /api/v1/reviews` без сессии и с неверными параметрами
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Неверный id репозитория среди нескольких

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `repository_id` дважды, второй раз не UUID
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `query.repository_id.1`
