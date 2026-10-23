## ADDED Requirements

### Requirement: Настройки репозитория описаны заглушкой

`GET` и `PUT /api/v1/repositories/{repository_id}/settings` SHALL быть описаны в схеме с полными моделями и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализованы. Ответ обеих — `RepositorySettingsResponse` с обязательными `auto_review_enabled`, `branch_filter` и `min_severity` из `FindingSeverity`. Обе SHALL требовать сессию, `PUT` — ещё `Origin` сервиса.

#### Scenario: Чтение настроек

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/settings` с сессией и UUID в пути
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Запись настроек

- **WHEN** вызывается `PUT /api/v1/repositories/{repository_id}/settings` с сессией, своим `Origin` и допустимым телом, в том числе с пустым `branch_filter`
- **THEN** ответ — `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Без сессии

- **WHEN** вызывается `GET` или `PUT` настроек без сессии (у `PUT` — со своим `Origin`)
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Запись без Origin

- **WHEN** вызывается `PUT` настроек без `Origin` или с чужим `Origin`, с сессией или без
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`, тело не разбирается

#### Scenario: Значения по умолчанию в контракте

- **WHEN** фронтенд читает описание `GET` настроек в `docs/openapi/openapi.json`
- **THEN** там указано: у репозитория без сохранённых настроек `auto_review_enabled = true`, `branch_filter = []` (все ветки), `min_severity = critical`

#### Scenario: Отказ по роли описан

- **WHEN** фронтенд читает `PUT` настроек в `docs/openapi/openapi.json`
- **THEN** ответ `403` допускает `ORIGIN_FORBIDDEN` и `REPOSITORY_MANAGE_REQUIRED`, а `auto_review_enabled` описан как та же колонка, что в `RepositoryResponse`

### Requirement: Тело записи настроек проверяется раньше 501

`PUT /api/v1/repositories/{repository_id}/settings` SHALL принимать `RepositorySettingsUpdate` — полный набор настроек: все три поля обязательны, лишние поля запрещены. Паттерн в `branch_filter` SHALL быть непустым и не длиннее 200 символов, паттернов SHALL быть не больше 50. Id репозитория и тело SHALL проверяться раньше ответа `501`.

#### Scenario: Неверный id

- **WHEN** вызывается `PUT` настроек с сессией, своим `Origin` и не-UUID в пути
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `path.repository_id`

#### Scenario: Неизвестная важность

- **WHEN** в теле `min_severity` вне `FindingSeverity`
- **THEN** ответ — `422` с ошибкой в `body.min_severity`

#### Scenario: Лишнее или пропущенное поле

- **WHEN** в теле есть поле вне модели или нет одного из трёх полей
- **THEN** ответ — `422` с `code = VALIDATION_ERROR`

#### Scenario: Пустой или длинный паттерн

- **WHEN** в `branch_filter` есть пустая строка или строка длиннее 200 символов
- **THEN** ответ — `422` с ошибкой в `body.branch_filter.<индекс>`

#### Scenario: Слишком много паттернов

- **WHEN** в `branch_filter` 51 паттерн
- **THEN** ответ — `422` с ошибкой в `body.branch_filter`

#### Scenario: Без сессии тело не проверяется

- **WHEN** вызывается `PUT` настроек со своим `Origin`, без сессии и с неверным телом
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

### Requirement: Статус правил репозитория описан заглушкой

`GET /api/v1/repositories/{repository_id}/rules` SHALL быть описан в схеме с ответом `RulesStatusResponse` — объединением по `status` со значениями `RulesStatus` — и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован. Ручка SHALL требовать сессию и проверять id репозитория раньше `501`.

#### Scenario: Статус правил

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/rules` с сессией и UUID в пути
- **THEN** ответ — `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Без сессии или с неверным id

- **WHEN** вызывается ручка без сессии или с сессией и не-UUID в пути
- **THEN** ответ — `401` с `code = UNAUTHENTICATED` или `422` с ошибкой в `path.repository_id`

#### Scenario: Варианты статуса в контракте

- **WHEN** фронтенд читает `RulesStatusResponse` в `docs/openapi/openapi.json`
- **THEN** у схемы дискриминатор `status` и четыре варианта: `loaded` (`branch`, `path`, `rules` из `RepoRule` с `name`, `patterns`, `excludes`, `checks`, `rejected` из `RejectedRule` с `name`, `reason`, `fetched_at` в UTC с `Z`), `absent`, `too_large`, `unavailable` (`branch`, `path`)
