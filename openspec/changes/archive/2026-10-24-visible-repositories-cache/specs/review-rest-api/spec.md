## ADDED Requirements

### Requirement: Набор видимых репозиториев кэшируется

Ответ GitHub о репозиториях, видимых пользователю, SHALL кэшироваться на `VISIBLE_REPOSITORIES_CACHE_TTL_SECONDS` (по умолчанию 30 с) для ручек видимости: `GET /reviews`, `GET /events`, ручек одного прогона и зарегистрированных репозиториев. Ключ SHALL зависеть от токена пользователя, сам токен SHALL NOT храниться. Подключён ли репозиторий, SHALL читаться из базы на каждый запрос. `GET /repositories/available` и подключение SHALL идти мимо кэша. Сбои GitHub SHALL NOT кэшироваться.

#### Scenario: Повтор в пределах TTL

- **WHEN** пользователь дважды за TTL запрашивает `GET /api/v1/reviews` или `GET /api/v1/repositories`
- **THEN** к GitHub за списком репозиториев был один запрос

#### Scenario: Подключённый репозиторий виден сразу

- **WHEN** пользователь подключает репозиторий и сразу запрашивает `GET /api/v1/repositories`
- **THEN** репозиторий в списке, без ожидания TTL

#### Scenario: Страница подключения без кэша

- **WHEN** пользователь запрашивает `GET /api/v1/repositories/available` в пределах TTL после другого запроса видимости
- **THEN** запрос к GitHub выполнен заново

#### Scenario: Сбой GitHub не запоминается

- **WHEN** GitHub не ответил, а следующий запрос пришёл в пределах TTL
- **THEN** следующий запрос снова идёт к GitHub

## MODIFIED Requirements

### Requirement: Список прогонов показывает только видимые репозитории

`GET /api/v1/reviews` SHALL отдавать только прогоны репозиториев, к которым у пользователя есть доступ на GitHub, — то же правило, что у `GET /repositories/available` и потока `GET /events`. Набор видимых SHALL считаться токеном пользователя; в пределах TTL кэша SHALL браться из кэша (требование «Набор видимых репозиториев кэшируется»); сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`.

#### Scenario: Прогон невидимого репозитория

- **WHEN** вызывается `GET /api/v1/reviews` с сессией, а среди подключённых репозиториев есть такой, которого GitHub не отдаёт по токену пользователя
- **THEN** прогонов этого репозитория нет ни в `items`, ни в `total`

#### Scenario: Невидимый репозиторий в фильтре

- **WHEN** вызывается `GET /api/v1/reviews` с `repository_id` невидимого или несуществующего репозитория
- **THEN** ответ — `200` с пустым `items` и `total = 0`, а не `404`

#### Scenario: GitHub недоступен

- **WHEN** вызывается `GET /api/v1/reviews` с сессией, а GitHub не ответил на запрос списка репозиториев
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

### Requirement: Ответы GitHub не выдают, есть ли прогон

Ручки одного прогона SHALL проверять видимость — ответ GitHub или его кэш — при любом id — до чтения прогона, и для несуществующего тоже: иначе сбой GitHub, отозванный токен или время ответа выдавали бы, что id есть. Сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`, отозванный токен — `401` с `code = UNAUTHENTICATED` и закрытую сессию, одинаково для существующего и несуществующего id.

#### Scenario: Несуществующий прогон

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` с id, которого нет
- **THEN** ответ — `404` с `code = REVIEW_NOT_FOUND`, видимость проверена так же, как для существующего: запрос к GitHub был, если в кэше ответа нет

#### Scenario: GitHub недоступен при проверке

- **WHEN** GitHub не ответил на запрос списка репозиториев — прогон с этим id есть или его нет
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

#### Scenario: Токен отозван на GitHub

- **WHEN** GitHub отклонил токен пользователя при проверке видимости
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта

### Requirement: Ответы GitHub не выдают, есть ли репозиторий

Ручки зарегистрированных репозиториев SHALL проверять видимость — ответ GitHub или его кэш — при любом id, до чтения базы. Сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`, отозванный токен — `401` с `code = UNAUTHENTICATED` и закрытую сессию. При `422` параметров запроса к GitHub SHALL NOT быть.

#### Scenario: GitHub недоступен

- **WHEN** вызывается `GET /api/v1/repositories` или `GET /api/v1/repositories/{repository_id}`, а GitHub не ответил
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

#### Scenario: Токен отозван на GitHub

- **WHEN** GitHub отклонил токен пользователя при проверке видимости
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта

#### Scenario: Неверный limit

- **WHEN** вызывается `GET /api/v1/repositories` с `limit=0`
- **THEN** ответ — `422`, запроса к GitHub не было
