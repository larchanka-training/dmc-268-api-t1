## RENAMED Requirements

- FROM: `### Requirement: Настройки репозитория описаны заглушкой`
- TO: `### Requirement: Настройки репозитория`

- FROM: `### Requirement: Тело записи настроек проверяется раньше 501`
- TO: `### Requirement: Тело записи настроек проверяется`

- FROM: `### Requirement: Статус правил репозитория описан заглушкой`
- TO: `### Requirement: Статус правил репозитория`

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

#### Scenario: Фильтр веток описан как неприменяемый

- **WHEN** фронтенд читает поле `branch_filter` в `docs/openapi/openapi.json`
- **THEN** в описании поля сказано, что фильтр сохраняется, но ревью пока его не применяет

### Requirement: Тело записи настроек проверяется

`PUT /api/v1/repositories/{repository_id}/settings` SHALL принимать `RepositorySettingsUpdate` — полный набор настроек: все три поля обязательны, лишние поля запрещены. Паттерн в `branch_filter` SHALL быть непустым после обрезки пробелов и не длиннее 200 символов, без повторов, паттернов SHALL быть не больше 50. Id репозитория и тело SHALL проверяться раньше обращения к базе и к GitHub.

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

#### Scenario: Паттерн из пробелов

- **WHEN** в `branch_filter` есть паттерн `"   "`
- **THEN** ответ — `422` с причиной «длина строки должна быть не меньше 1» в `body.branch_filter.<индекс>`

#### Scenario: Повтор паттерна

- **WHEN** в `branch_filter` есть `main` и ` main ` (совпадают после обрезки пробелов)
- **THEN** ответ — `422` с причиной «паттерны веток повторяются» в `body.branch_filter`

#### Scenario: Неверное тело не доходит до GitHub

- **WHEN** вызывается `PUT` настроек с сессией, своим `Origin` и неверным телом
- **THEN** ответ — `422`, запросов к GitHub и записи в базу нет

#### Scenario: Паттерн хранится обрезанным

- **WHEN** в `branch_filter` записан ` release/* `
- **THEN** `GET` настроек отдаёт `release/*`

### Requirement: Статус правил репозитория

`GET /api/v1/repositories/{repository_id}/rules` SHALL отдавать `200` с `RulesStatusResponse` — исходом загрузки `.review/rules.md` с последнего коммита ветки по умолчанию репозитория, с теми же статусами и тем же разбором, что у загрузки правил для прогона. `branch` SHALL быть веткой по умолчанию, `path` — `.review/rules.md`. Ручка SHALL требовать сессию.

#### Scenario: Статус правил

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/rules` с сессией для видимого репозитория
- **THEN** ответ — `200` с одним из вариантов `RulesStatusResponse`, `branch` — ветка по умолчанию, `path` — `.review/rules.md`

#### Scenario: Файл разобран

- **WHEN** на ветке по умолчанию есть `.review/rules.md` с принятыми правилами и одним отклонённым разделом
- **THEN** ответ — `200` со `status = loaded`, правилами по порядку в файле, отклонённым разделом с причиной и `fetched_at` в UTC с `Z`

#### Scenario: Ни один раздел не стал правилом

- **WHEN** файл есть, но ни один раздел не стал правилом
- **THEN** ответ — `200` со `status = empty`, отклонёнными разделами и `fetched_at`

#### Scenario: Файла нет

- **WHEN** на ветке по умолчанию нет `.review/rules.md`
- **THEN** ответ — `200` со `status = absent`, `branch` и `path`

#### Scenario: Файл больше лимита

- **WHEN** файл больше лимита размера правил
- **THEN** ответ — `200` со `status = too_large`, файл не разбирается

#### Scenario: GitHub не отдал файл

- **WHEN** GitHub не ответил на запрос установки приложения, на разрешение ветки в коммит или на чтение файла
- **THEN** ответ — `200` со `status = unavailable`, а не ошибка

#### Scenario: Без сессии или с неверным id

- **WHEN** вызывается ручка без сессии или с сессией и не-UUID в пути
- **THEN** ответ — `401` с `code = UNAUTHENTICATED` или `422` с ошибкой в `path.repository_id`

#### Scenario: Варианты статуса в контракте

- **WHEN** фронтенд читает `RulesStatusResponse` в `docs/openapi/openapi.json`
- **THEN** у схемы дискриминатор `status` и пять вариантов: `loaded` (`branch`, `path`, `rules` из `RepoRule` с `name`, `patterns`, `excludes`, `checks`, `rejected` из `RejectedRule` с `name`, `reason`, `fetched_at` в UTC с `Z`), `empty` (`branch`, `path`, `rejected`, `fetched_at`), `absent`, `too_large`, `unavailable` (`branch`, `path`)

## ADDED Requirements

### Requirement: Запись настроек требует роль на репозитории

`PUT /api/v1/repositories/{repository_id}/settings` SHALL проверять роль пользователя запросом к GitHub мимо кэша видимости, тем же правилом, что выключение репозитория. Без роли admin или maintain SHALL отвечать `403` с `code = REPOSITORY_MANAGE_REQUIRED` и SHALL NOT менять настройки.

#### Scenario: Роль write

- **WHEN** пользователь с ролью write на репозитории вызывает `PUT` настроек с допустимым телом
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, `GET` настроек отдаёт прежние значения

#### Scenario: Роль отозвана после кэширования видимости

- **WHEN** репозиторий в кэше видимости пользователя, а GitHub на прямой запрос отдаёт роль без admin и maintain
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`

#### Scenario: Выключенный репозиторий

- **WHEN** пользователь с ролью admin вызывает `PUT` настроек репозитория, выключенного `DELETE /api/v1/repositories/{repository_id}`
- **THEN** ответ — `200`, настройки сохранены, репозиторий остаётся выключенным

### Requirement: Отказы настроек и статуса правил

`GET` и `PUT …/settings` и `GET …/rules` SHALL отвечать `404` с `code = REPOSITORY_NOT_FOUND`, если репозитория нет или пользователь не видит его на GitHub (кэш видимости, как у `GET /api/v1/repositories/{id}`), — ответ один и тот же. Сбой GitHub на проверке видимости или роли SHALL давать `502` с `code = SCM_UNAVAILABLE`, а непригодный токен пользователя — `401` с закрытием сессии.

#### Scenario: Нет такого репозитория

- **WHEN** вызывается любая из трёх ручек с UUID, которого нет среди репозиториев
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND`

#### Scenario: Репозиторий не виден пользователю

- **WHEN** репозиторий зарегистрирован, но пользователь не видит его на GitHub
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND`, как у несуществующего, и к файлу правил запроса нет

#### Scenario: GitHub не ответил на проверке роли

- **WHEN** на `PUT` настроек GitHub отвечает `5xx` на прямой запрос репозитория
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, настройки не меняются

#### Scenario: Отказы в контракте

- **WHEN** фронтенд читает три ручки в `docs/openapi/openapi.json`
- **THEN** среди ответов есть `404 REPOSITORY_NOT_FOUND` и `502 SCM_UNAVAILABLE`, у `GET …/rules` `404` допускает ещё `REPOSITORY_NOT_IN_INSTALLATION`, и нет `501 NOT_IMPLEMENTED`

### Requirement: Статус правил требует установку приложения

`GET /api/v1/repositories/{repository_id}/rules` SHALL отвечать `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, если приложение не установлено на видимый пользователю репозиторий или установка приостановлена, — как дифф прогона. Ветка в этом случае SHALL NOT разрешаться, файл SHALL NOT запрашиваться.

#### Scenario: Приложение не установлено

- **WHEN** вызывается `GET …/rules` для видимого репозитория, на котором приложение не установлено или установка приостановлена
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, запросов разрешения ветки и файла нет

#### Scenario: Сбой GitHub на запросе установки

- **WHEN** GitHub отвечает `5xx` на запрос установки приложения на репозиторий
- **THEN** ответ — `200` со `status = unavailable`, а не `404`

### Requirement: Статус правил кэшируется по коммиту

`GET …/rules` SHALL разрешать ветку по умолчанию в sha у GitHub на каждый запрос, а исход загрузки файла SHALL брать из кэша правил по паре «репозиторий, sha»: на том же коммите файл повторно не запрашивается, и `fetched_at` остаётся временем первого получения. Сбой GitHub SHALL NOT кэшироваться.

#### Scenario: Повтор на том же коммите

- **WHEN** `GET …/rules` вызывается дважды, а ветка по умолчанию не сдвинулась
- **THEN** файл у GitHub запрошен один раз, оба ответа совпадают, включая `fetched_at`

#### Scenario: Новый коммит в ветке по умолчанию

- **WHEN** между двумя вызовами в ветку по умолчанию пришёл коммит
- **THEN** второй вызов запрашивает файл на новом коммите, и `fetched_at` — время второго получения

#### Scenario: Сбой не кэшируется

- **WHEN** первый вызов получил `unavailable`, а GitHub восстановился
- **THEN** второй вызов запрашивает файл снова и отдаёт исход загрузки
