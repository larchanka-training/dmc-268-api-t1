## REMOVED Requirements

### Requirement: Подключение репозитория описано заглушками

**Reason**: ручки реализованы и больше не отвечают `501`.

**Migration**: поведение описывают требования «Список репозиториев, доступных для подключения» и «Подключение репозитория»; схема запроса и ответа та же.

## ADDED Requirements

### Requirement: Список репозиториев, доступных для подключения

`GET /api/v1/repositories/available` SHALL требовать сессию и SHALL отдавать репозитории всех установок GitHub App: `provider = github`, `provider_id`, `full_name`, `default_branch` и `already_connected` — есть ли репозиторий в `repositories` по `(provider, provider_id)`. По правам вошедшего пользователя список SHALL NOT фильтроваться: права проверяет подключение. Сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`.

#### Scenario: Список с отметкой подключённых

- **WHEN** в установках приложения есть репозитории `octo-org/api` и `octo-org/web`, а `octo-org/api` уже зарегистрирован, и вызывается `GET /api/v1/repositories/available` с сессией
- **THEN** ответ — `200` с обоими репозиториями, у `octo-org/api` `already_connected = true`, у `octo-org/web` — `false`

#### Scenario: Список без сессии

- **WHEN** вызывается `GET /api/v1/repositories/available` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: GitHub недоступен при получении списка

- **WHEN** GitHub не ответил или ответил ошибкой
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

### Requirement: Подключение репозитория

`POST /api/v1/repositories` SHALL требовать сессию и `Origin` сервиса и SHALL регистрировать репозиторий установки GitHub App: `auto_review_enabled = true`, `full_name` и `default_branch` — из данных установки, ответ — `201` с `RepositoryResponse`. `installation_id` SHALL NOT храниться: его даёт список установок приложения. Сбой GitHub SHALL давать `502` `SCM_UNAVAILABLE` без записи.

#### Scenario: Подключение администратором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией пользователя с правами admin, своим `Origin` и телом `{"provider": "github", "provider_id": "123456789"}` репозитория установки
- **THEN** ответ — `201` с репозиторием, `auto_review_enabled = true`
- **AND** репозиторий записан в `repositories`

### Requirement: Отказы подключения репозитория

Подключать репозиторий SHALL только пользователь с правами admin на него у провайдера. Проверки SHALL идти по порядку: провайдер не `github` или репозитория нет среди репозиториев установок — `404 REPOSITORY_NOT_IN_INSTALLATION`; репозиторий уже зарегистрирован, в том числе параллельным запросом, — `409 REPOSITORY_ALREADY_CONNECTED`; нет прав admin — `403 REPOSITORY_ADMIN_REQUIRED`.

#### Scenario: Подключение без прав admin

- **WHEN** у вошедшего пользователя права на репозиторий ниже admin или GitHub отвечает, что он не коллаборатор
- **THEN** ответ — `403` с `code = REPOSITORY_ADMIN_REQUIRED`, записи нет

#### Scenario: Повторное подключение

- **WHEN** репозиторий уже зарегистрирован
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
