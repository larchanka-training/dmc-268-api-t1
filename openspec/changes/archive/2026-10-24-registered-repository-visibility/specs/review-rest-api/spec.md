## ADDED Requirements

### Requirement: Репозиторий виден только тому, кто видит его на GitHub

`GET /api/v1/repositories` SHALL отдавать только репозитории, которые пользователь видит на GitHub, — то же правило, что у прогонов, `GET /reviews` и `GET /events`. `GET /api/v1/repositories/{repository_id}` и `/pull-requests` SHALL отвечать для невидимого репозитория тем же `404` с `code = REPOSITORY_NOT_FOUND` и тем же `detail`, что для несуществующего. Репозиторий не с GitHub SHALL не быть виден.

#### Scenario: Список без чужих репозиториев

- **WHEN** зарегистрированы два репозитория, а GitHub по токену пользователя отдаёт только один
- **THEN** `GET /api/v1/repositories` возвращает только его

#### Scenario: Чужой репозиторий по id

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}` или `/pull-requests` для репозитория, которого GitHub пользователю не отдаёт
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND` и тем же `detail`, что у несуществующего

### Requirement: Ответы GitHub не выдают, есть ли репозиторий

Ручки зарегистрированных репозиториев SHALL обращаться к GitHub за видимостью при любом id, до чтения базы. Сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`, отозванный токен — `401` с `code = UNAUTHENTICATED` и закрытую сессию. При `422` параметров запроса к GitHub SHALL NOT быть.

#### Scenario: GitHub недоступен

- **WHEN** вызывается `GET /api/v1/repositories` или `GET /api/v1/repositories/{repository_id}`, а GitHub не ответил
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

#### Scenario: Токен отозван на GitHub

- **WHEN** GitHub отклонил токен пользователя при проверке видимости
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта

#### Scenario: Неверный limit

- **WHEN** вызывается `GET /api/v1/repositories` с `limit=0`
- **THEN** ответ — `422`, запроса к GitHub не было
