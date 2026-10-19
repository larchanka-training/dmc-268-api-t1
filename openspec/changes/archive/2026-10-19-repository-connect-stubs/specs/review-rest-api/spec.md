## ADDED Requirements

### Requirement: Подключение репозитория описано заглушками

`GET /api/v1/repositories/available` и `POST /api/v1/repositories` SHALL быть описаны в схеме с полными моделями запроса и ответа и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока подключение не реализовано. Сессию, а для `POST` также `Origin` и тело, они SHALL проверять так же, как реализованные ручки.

#### Scenario: Список доступных репозиториев

- **WHEN** вызывается `GET /api/v1/repositories/available` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Подключение репозитория

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и телом `{"provider": "github", "providerId": "123456789"}`
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Подключение без Origin

- **WHEN** вызывается `POST /api/v1/repositories` без `Origin`
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`

#### Scenario: Подключение с пустым идентификатором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и пустым `providerId`
- **THEN** ответ — `422` с `{"where": "body.providerId", "reason": "длина строки должна быть не меньше 1"}`
