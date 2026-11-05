## MODIFIED Requirements

### Requirement: Настройки репозитория

`GET /api/v1/repositories/{repository_id}/settings` SHALL отдавать `200` с `RepositorySettingsResponse` — сохранёнными настройками ревью репозитория, а у репозитория, которому их не сохраняли, — `auto_review_enabled = true`, `branch_filter = []`, `min_severity = critical`. `PUT` SHALL заменять настройки телом целиком и отвечать `200` с сохранёнными. Обе SHALL требовать сессию, `PUT` — ещё `Origin` сервиса.

#### Scenario: Чтение настроек по умолчанию

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/settings` с сессией для видимого репозитория, которому настройки не сохраняли
- **THEN** ответ — `200` с `auto_review_enabled = true`, `branch_filter = []`, `min_severity = critical`

#### Scenario: Чтение настроек

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/settings` с сессией для видимого репозитория с сохранёнными настройками
- **THEN** ответ — `200` с сохранёнными `auto_review_enabled`, `branch_filter` и `min_severity`

#### Scenario: Запись настроек

- **WHEN** пользователь с ролью admin или maintain вызывает `PUT` настроек со своим `Origin` и телом `{"auto_review_enabled": false, "branch_filter": ["main", "release/*"], "min_severity": "high"}`
- **THEN** ответ — `200` с тем же телом, и следующий `GET` настроек отдаёт его же

#### Scenario: Полная замена

- **WHEN** после записи `branch_filter = ["main"]` вызывается `PUT` с `branch_filter = []`
- **THEN** `GET` настроек отдаёт `branch_filter = []`, прежний паттерн не остаётся

#### Scenario: Повтор того же тела

- **WHEN** `PUT` с тем же телом вызывается второй раз
- **THEN** ответ — `200` с тем же телом, сохранённые настройки не меняются

#### Scenario: Та же колонка, что в репозитории

- **WHEN** `PUT` настроек записывает `auto_review_enabled = false`
- **THEN** `GET /api/v1/repositories/{repository_id}` отдаёт `auto_review_enabled = false`

#### Scenario: Настройки после повторного включения

- **WHEN** репозиторию сохранили `auto_review_enabled = false`, `min_severity = high` и `branch_filter = ["main"]`, его выключили `DELETE /api/v1/repositories/{repository_id}` и снова подключили `POST /api/v1/repositories`
- **THEN** `GET` настроек отдаёт сохранённые `auto_review_enabled = false`, `branch_filter = ["main"]`, `min_severity = high`

#### Scenario: Без сессии

- **WHEN** вызывается `GET` или `PUT` настроек без сессии (у `PUT` — со своим `Origin`)
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Запись без Origin

- **WHEN** вызывается `PUT` настроек без `Origin` или с чужим `Origin`, с сессией или без
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`, тело не разбирается

#### Scenario: Значения по умолчанию в контракте

- **WHEN** фронтенд читает описание `GET` настроек в `docs/openapi/openapi.json`
- **THEN** там указано: у репозитория без сохранённых настроек `auto_review_enabled = true`, `branch_filter = []` (все ветки), `min_severity = critical`, а `501` среди ответов нет

#### Scenario: Отказ по роли описан

- **WHEN** фронтенд читает `PUT` настроек в `docs/openapi/openapi.json`
- **THEN** ответ `403` допускает `ORIGIN_FORBIDDEN` и `REPOSITORY_MANAGE_REQUIRED`, а `auto_review_enabled` описан как та же колонка, что в `RepositoryResponse`

#### Scenario: Фильтр веток описан

- **WHEN** фронтенд читает поле `branch_filter` в `docs/openapi/openapi.json`
- **THEN** в описании поля сказано, как совпадают `*` и `**`, что регистр учитывается, что PR вне фильтра получает check run `success` «Ветка не в фильтре ревью» и что ручной запуск фильтр не применяет
