## MODIFIED Requirements

### Requirement: Отказы подключения репозитория

Подключать репозиторий SHALL только пользователь с `permissions.admin` или `permissions.maintain`. Порядок: не `github`, не виден пользователю или публичный только с `pull`, приложение не установлено или приостановлено — `404 REPOSITORY_NOT_IN_INSTALLATION` (установка — после доступа); нет admin и maintain — `403 REPOSITORY_MANAGE_REQUIRED`; уже зарегистрирован — `409 REPOSITORY_ALREADY_CONNECTED`. Все `404` SHALL быть одинаковы; без прав SHALL NOT быть видно, подключён ли репозиторий.

#### Scenario: Подключение с ролью maintain

- **WHEN** у вошедшего пользователя на репозиторий `permissions.maintain = true` и `permissions.admin = false`
- **THEN** ответ — `201`, репозиторий записан

#### Scenario: Подключение без прав admin

- **WHEN** GitHub отдаёт репозиторий пользователю с `permissions.push = true` или `permissions.triage = true`, но с `permissions.admin = false` и `permissions.maintain = false`
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, записи нет

#### Scenario: Репозиторий только с правом чтения

- **WHEN** GitHub отдаёт публичный (`private = false`) репозиторий по id с `permissions.push`, `triage`, `maintain` и `admin` равными `false` — так он отдаёт любому чужой публичный репозиторий
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, тело совпадает с ответом на отсутствующий репозиторий, кроме `trace_id`, установка приложения не запрашивается, и ответ не зависит от того, установлено ли туда приложение

#### Scenario: Приватный репозиторий только с правом чтения

- **WHEN** GitHub отдаёт приватный (`private = true`) репозиторий по id с `permissions.push`, `triage`, `maintain` и `admin` равными `false` — у пользователя роль read, а приложение на репозитории установлено
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, записи нет: приватный репозиторий GitHub отдаёт только участнику, и отказ ему ничего не раскрывает

#### Scenario: Приватный репозиторий только с правом чтения без приложения

- **WHEN** у пользователя роль read на приватном репозитории, а приложение на нём не установлено или установка приостановлена
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`

#### Scenario: Подключение уже подключённого без прав

- **WHEN** репозиторий уже зарегистрирован, а у вошедшего пользователя нет ни admin, ни maintain на него
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, а не `409`

#### Scenario: Повторное подключение

- **WHEN** репозиторий уже зарегистрирован, и подключает пользователь с admin или maintain
- **THEN** ответ — `409` с `code = REPOSITORY_ALREADY_CONNECTED`

#### Scenario: Репозиторий не из установки

- **WHEN** `provider_id` нет среди репозиториев установок приложения
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`

#### Scenario: Невидимый репозиторий неотличим от отсутствующего

- **WHEN** репозиторий есть в установке, но GitHub не отдаёт его по токену пользователя
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, тело совпадает с ответом на отсутствующий репозиторий, кроме `trace_id`

#### Scenario: Приложение не установлено или установка приостановлена

- **WHEN** пользователь видит репозиторий, но приложение на нём не установлено или установка приостановлена
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, тело совпадает с ответом на невидимый репозиторий, кроме `trace_id`

#### Scenario: Установка не проверяется для чужого репозитория

- **WHEN** пользователь не видит репозиторий
- **THEN** установка приложения на нём не запрашивается, ответ — `404`

#### Scenario: GitHub недоступен при подключении

- **WHEN** GitHub не ответил или ответил ошибкой, кроме `401`
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, записи нет

#### Scenario: Подключение без Origin

- **WHEN** вызывается `POST /api/v1/repositories` без `Origin`
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`

#### Scenario: Подключение с пустым идентификатором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и пустым `provider_id`
- **THEN** ответ — `422` с `{"where": "body.provider_id", "reason": "длина строки должна быть не меньше 1"}`
