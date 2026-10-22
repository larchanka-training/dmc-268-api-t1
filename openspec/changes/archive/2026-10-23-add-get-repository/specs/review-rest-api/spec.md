## ADDED Requirements

### Requirement: Один репозиторий по id

`GET /api/v1/repositories/{repository_id}` SHALL требовать сессию и SHALL отдавать зарегистрированный репозиторий в той же форме `RepositoryResponse`, что строка `GET /api/v1/repositories`. Репозитория нет — `404` с `code = REPOSITORY_NOT_FOUND`; id не UUID — `422`.

#### Scenario: Репозиторий по id

- **WHEN** репозиторий зарегистрирован, и вызывается `GET /api/v1/repositories/{repository_id}` с сессией
- **THEN** ответ — `200`, тело совпадает со строкой этого репозитория в `GET /api/v1/repositories`

#### Scenario: Репозиторий за пределами первой страницы списка

- **WHEN** зарегистрировано больше репозиториев, чем помещается на страницу списка, и запрашивается последний по id
- **THEN** ответ — `200` с этим репозиторием

#### Scenario: Нет такого репозитория

- **WHEN** репозитория с таким id нет
- **THEN** ответ — `404` `application/problem+json` с `code = REPOSITORY_NOT_FOUND`

#### Scenario: id не UUID

- **WHEN** вызывается `GET /api/v1/repositories/repo-1` с сессией
- **THEN** ответ — `422` с ошибкой в `path.repository_id`

#### Scenario: Без сессии

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`
