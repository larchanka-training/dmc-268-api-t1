## MODIFIED Requirements

### Requirement: Список репозиториев, доступных для подключения

`GET /api/v1/repositories/available` SHALL требовать сессию и SHALL отдавать репозитории установок GitHub App, которые GitHub отдаёт по токену пользователя сессии (`/user/installations…`), с `already_connected` — есть ли репозиторий в `repositories`. Репозиторий, которого GitHub пользователю не отдал, SHALL NOT попадать в ответ. Приостановленная установка SHALL пропускаться. Сбой GitHub и неожиданное тело SHALL давать `502 SCM_UNAVAILABLE`.

#### Scenario: Список с отметкой подключённых

- **WHEN** по токену пользователя GitHub отдаёт репозитории `octo-org/api` и `octo-org/web`, `octo-org/api` уже зарегистрирован, и вызывается `GET /api/v1/repositories/available` с сессией
- **THEN** ответ — `200` с обоими репозиториями, у `octo-org/api` `already_connected = true`, у `octo-org/web` — `false`

#### Scenario: Репозиторий только с правом чтения показывается

- **WHEN** GitHub отдаёт `octo-org/web` с `permissions.admin = false` и `permissions.maintain = false`
- **THEN** `octo-org/web` есть в ответе

#### Scenario: Репозиторий без доступа не показывается

- **WHEN** `octo-org/api` есть в установке приложения, но у вошедшего пользователя нет к нему доступа, и GitHub не отдаёт его по токену пользователя
- **THEN** `octo-org/api` в ответе нет, ответ — `200`

#### Scenario: Чужой приватный репозиторий не показывается

- **WHEN** `octo-org/secret` приватный, в установке приложения, а вошедший пользователь к нему доступа не имеет
- **THEN** `octo-org/secret` в ответе нет: сервис показывает только то, что GitHub отдал по токену пользователя, и сам доступ не вычисляет

#### Scenario: Приостановленная установка

- **WHEN** одна из установок пользователя приостановлена (`suspended_at` не `null`)
- **THEN** её репозитории не запрашиваются, ответ — `200` с репозиториями остальных установок

#### Scenario: Доступа нет ни к одному репозиторию

- **WHEN** GitHub не отдаёт пользователю ни одного репозитория установок
- **THEN** ответ — `200` с пустым `items`

#### Scenario: Список без сессии

- **WHEN** вызывается `GET /api/v1/repositories/available` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: GitHub недоступен при получении списка

- **WHEN** GitHub не ответил, ответил `429`, `5xx` или другой ошибкой, кроме `401`, или вернул тело без нужных полей, в том числе `null` в них
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, сессия не закрывается

### Requirement: Подключение репозитория

`POST /api/v1/repositories` SHALL требовать сессию и `Origin` сервиса и SHALL регистрировать репозиторий из списка, который GitHub отдаёт по токену пользователя сессии: `auto_review_enabled = true`, `full_name` и `default_branch` — из ответа GitHub, ответ — `201` с `RepositoryResponse`. `installation_id` SHALL NOT храниться. Сбой GitHub SHALL давать `502` `SCM_UNAVAILABLE` без записи.

#### Scenario: Подключение администратором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией пользователя, у которого на репозиторий `permissions.admin = true`, своим `Origin` и телом `{"provider": "github", "provider_id": "123456789"}`
- **THEN** ответ — `201` с репозиторием, `auto_review_enabled = true`
- **AND** репозиторий записан в `repositories`

### Requirement: Отказы подключения репозитория

Подключать репозиторий SHALL только пользователь с `permissions.admin` или `permissions.maintain` в ответе GitHub. Порядок проверок: не `github` или нет в списке пользователя — `404 REPOSITORY_NOT_IN_INSTALLATION`; нет admin и maintain — `403 REPOSITORY_MANAGE_REQUIRED`; уже зарегистрирован, в том числе параллельно, — `409 REPOSITORY_ALREADY_CONNECTED`. Отсутствующий и невидимый репозиторий SHALL получать одинаковый ответ; пользователь без прав SHALL NOT узнавать, подключён ли репозиторий.

#### Scenario: Подключение с ролью maintain

- **WHEN** у вошедшего пользователя на репозиторий `permissions.maintain = true` и `permissions.admin = false`
- **THEN** ответ — `201`, репозиторий записан

#### Scenario: Подключение без прав admin

- **WHEN** GitHub отдаёт репозиторий пользователю с `permissions.admin = false` и `permissions.maintain = false`
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, записи нет

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

#### Scenario: GitHub недоступен при подключении

- **WHEN** GitHub не ответил или ответил ошибкой, кроме `401`
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, записи нет

#### Scenario: Подключение без Origin

- **WHEN** вызывается `POST /api/v1/repositories` без `Origin`
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`

#### Scenario: Подключение с пустым идентификатором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и пустым `provider_id`
- **THEN** ответ — `422` с `{"where": "body.provider_id", "reason": "длина строки должна быть не меньше 1"}`

## ADDED Requirements

### Requirement: Истёкший токен пользователя обновляется

Ручки, обращающиеся к GitHub по токену пользователя (`GET /api/v1/repositories/available`, `POST /api/v1/repositories`), SHALL обменивать истёкший access token по refresh token и сохранять новую пару. Сбой GitHub при обмене SHALL давать `502 SCM_UNAVAILABLE` без закрытия сессии. Два параллельных запроса одной сессии SHALL обменивать refresh token не больше одного раза.

#### Scenario: Истёкший токен обновляется

- **WHEN** access token сессии истёк, refresh token действует, и вызывается `GET /api/v1/repositories/available`
- **THEN** токен обменивается по refresh token, новая пара сохраняется, ответ — `200`

#### Scenario: Токен без истечения

- **WHEN** GitHub выдал при входе токен без `expires_in`
- **THEN** токен используется без обмена, сколько бы времени ни прошло

#### Scenario: GitHub недоступен при обмене

- **WHEN** обмен refresh token не удался из-за сети, `5xx` или ошибки настройки приложения
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, сессия и токен остаются

#### Scenario: Два запроса одной сессии с истёкшим токеном

- **WHEN** два запроса одной сессии одновременно видят истёкший access token
- **THEN** refresh token обменивается один раз, оба запроса используют новый токен

### Requirement: Непригодный токен пользователя закрывает сессию

Если у сессии нет токена пользователя, он не расшифровывается, refresh token истёк или отвергнут GitHub, или GitHub ответил `401` на токен, ручки репозиториев SHALL удалять сессию вместе с токеном и отвечать `401 UNAUTHENTICATED` с удалением cookie `session`.

#### Scenario: Отвергнутый refresh token

- **WHEN** GitHub отвечает на обмен `bad_refresh_token`
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, cookie `session` удаляется, следующий запрос с ней получает `401`

#### Scenario: Отозванный токен

- **WHEN** GitHub отвечает `401` на запрос с токеном пользователя
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта

#### Scenario: Сессия, открытая до хранения токенов

- **WHEN** у сессии нет сохранённого токена
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта, пользователь входит заново
