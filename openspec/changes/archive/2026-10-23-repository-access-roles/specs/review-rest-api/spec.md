## MODIFIED Requirements

### Requirement: Список репозиториев, доступных для подключения

`GET /api/v1/repositories/available` SHALL требовать сессию и SHALL отдавать репозитории установок GitHub App, к которым у вошедшего пользователя есть доступ у провайдера (любая роль), с `already_connected` — есть ли репозиторий в `repositories`. Репозиторий без доступа SHALL NOT попадать в ответ, это не ошибка. Сбой GitHub, в том числе при запросе роли, SHALL давать `502 SCM_UNAVAILABLE`.

#### Scenario: Список с отметкой подключённых

- **WHEN** в установках приложения есть репозитории `octo-org/api` и `octo-org/web`, у вошедшего пользователя есть доступ к обоим, `octo-org/api` уже зарегистрирован, и вызывается `GET /api/v1/repositories/available` с сессией
- **THEN** ответ — `200` с обоими репозиториями, у `octo-org/api` `already_connected = true`, у `octo-org/web` — `false`

#### Scenario: Репозиторий только с правом чтения показывается

- **WHEN** у вошедшего пользователя роль read на `octo-org/web`
- **THEN** `octo-org/web` есть в ответе

#### Scenario: Репозиторий без доступа не показывается

- **WHEN** GitHub отвечает, что у вошедшего пользователя нет доступа к `octo-org/api` (роль `none` или `404`)
- **THEN** `octo-org/api` в ответе нет, ответ — `200`

#### Scenario: Доступа нет ни к одному репозиторию

- **WHEN** у вошедшего пользователя нет доступа ни к одному репозиторию установок
- **THEN** ответ — `200` с пустым `items`

#### Scenario: Список без сессии

- **WHEN** вызывается `GET /api/v1/repositories/available` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: GitHub недоступен при получении списка

- **WHEN** GitHub не ответил или ответил ошибкой на запрос списка установок или роли пользователя
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

### Requirement: Подключение репозитория

`POST /api/v1/repositories` SHALL требовать сессию и `Origin` сервиса и SHALL регистрировать репозиторий установки GitHub App: `auto_review_enabled = true`, `full_name` и `default_branch` — из данных установки, ответ — `201` с `RepositoryResponse`. `installation_id` SHALL NOT храниться: его даёт список установок приложения. Сбой GitHub SHALL давать `502` `SCM_UNAVAILABLE` без записи.

#### Scenario: Подключение администратором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией пользователя с ролью admin, своим `Origin` и телом `{"provider": "github", "provider_id": "123456789"}` репозитория установки
- **THEN** ответ — `201` с репозиторием, `auto_review_enabled = true`
- **AND** репозиторий записан в `repositories`

### Requirement: Отказы подключения репозитория

Подключать репозиторий SHALL только пользователь с ролью admin или maintain у провайдера. Проверки SHALL идти по порядку: не `github` или нет в установках — `404 REPOSITORY_NOT_IN_INSTALLATION`; нет роли admin или maintain — `403 REPOSITORY_MANAGE_REQUIRED`; уже зарегистрирован, в том числе параллельным запросом, — `409 REPOSITORY_ALREADY_CONNECTED`. Пользователь без прав SHALL NOT узнавать из отказа, подключён ли репозиторий.

#### Scenario: Подключение с ролью maintain

- **WHEN** у вошедшего пользователя роль maintain на репозиторий установки
- **THEN** ответ — `201`, репозиторий записан

#### Scenario: Подключение без прав admin

- **WHEN** у вошедшего пользователя нет ни роли admin, ни maintain: роль write, triage, read или пользовательская роль, нет доступа или GitHub отвечает, что он не коллаборатор
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, записи нет

#### Scenario: Подключение уже подключённого без прав

- **WHEN** репозиторий уже зарегистрирован, а у вошедшего пользователя нет роли admin или maintain на него
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, а не `409`

#### Scenario: Повторное подключение

- **WHEN** репозиторий уже зарегистрирован, и подключает пользователь с ролью admin или maintain
- **THEN** ответ — `409` с `code = REPOSITORY_ALREADY_CONNECTED`

#### Scenario: Репозиторий не из установки

- **WHEN** `provider_id` нет среди репозиториев установок приложения
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`

#### Scenario: GitHub недоступен при подключении

- **WHEN** GitHub не ответил или ответил ошибкой
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, записи нет

#### Scenario: Подключение без Origin

- **WHEN** вызывается `POST /api/v1/repositories` без `Origin`
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`

#### Scenario: Подключение с пустым идентификатором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и пустым `provider_id`
- **THEN** ответ — `422` с `{"where": "body.provider_id", "reason": "длина строки должна быть не меньше 1"}`
