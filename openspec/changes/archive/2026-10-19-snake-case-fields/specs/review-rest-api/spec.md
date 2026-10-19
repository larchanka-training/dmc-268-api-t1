## MODIFIED Requirements

### Requirement: Тело запроса проверяется строго

Тело изменяющего запроса SHALL приниматься только с полями из схемы в snake_case и без приведения типов: число строкой, строка числом, ключ в camelCase и лишнее поле получают `422` `VALIDATION_ERROR`. Строки и числа SHALL укладываться в границы колонок. Имя лишнего поля SHALL NOT попадать в ответ: `where` указывает на объект, `reason` — «лишнее поле».

#### Scenario: Число строкой

- **WHEN** `POST /api/v1/reviews` с сессией и своим `Origin` пришёл с `"pull_request_number": "5"`
- **THEN** ответ — `422` с `{"where": "body.pull_request_number", "reason": "ожидается целое число"}`

#### Scenario: Ключ в camelCase

- **WHEN** `POST /api/v1/reviews` пришёл с `"providerRepositoryId"` вместо `"provider_repository_id"`
- **THEN** ответ — `422`: `provider_repository_id` не передан, а `providerRepositoryId` — лишнее поле

#### Scenario: Лишнее поле

- **WHEN** тело `POST /api/v1/reviews` содержит поле `<img src=x onerror=alert(1)>`
- **THEN** ответ — `422` с `{"where": "body", "reason": "лишнее поле"}`, имени поля в ответе нет

### Requirement: Подключение репозитория описано заглушками

`GET /api/v1/repositories/available` и `POST /api/v1/repositories` SHALL быть описаны в схеме с полными моделями запроса и ответа и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока подключение не реализовано. Сессию, а для `POST` также `Origin` и тело, они SHALL проверять так же, как реализованные ручки.

#### Scenario: Список доступных репозиториев

- **WHEN** вызывается `GET /api/v1/repositories/available` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Подключение репозитория

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и телом `{"provider": "github", "provider_id": "123456789"}`
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Подключение без Origin

- **WHEN** вызывается `POST /api/v1/repositories` без `Origin`
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`

#### Scenario: Подключение с пустым идентификатором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и пустым `provider_id`
- **THEN** ответ — `422` с `{"where": "body.provider_id", "reason": "длина строки должна быть не меньше 1"}`
