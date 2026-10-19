## ADDED Requirements

### Requirement: Ошибки ручек отдаются в формате ApiError

Любая ошибка ручки `/api/v1` SHALL отдаваться телом `ApiError` с полями `code` и `message`. Ненайденная сущность SHALL получать `404` с кодом по сущности: `REVIEW_NOT_FOUND` для прогона и его потока, `REPOSITORY_NOT_FOUND` для репозитория, `PROVIDER_NOT_SUPPORTED` для провайдера без входа. Параметр пути или запроса, не прошедший проверку, SHALL получать `422` с `VALIDATION_ERROR`; `message` SHALL называть параметр и причину и SHALL NOT повторять переданное значение.

#### Scenario: Несуществующий прогон

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` или `GET /api/v1/reviews/{review_run_id}/events` для прогона, которого нет
- **THEN** ответ — `404` с `ApiError.code = REVIEW_NOT_FOUND`

#### Scenario: Несуществующий репозиторий

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/pull-requests` для репозитория, которого нет
- **THEN** ответ — `404` с `ApiError.code = REPOSITORY_NOT_FOUND`

#### Scenario: Неподдержанный провайдер входа

- **WHEN** браузер запрашивает `GET /api/v1/auth/bitbucket/login`
- **THEN** ответ — `404` с `ApiError.code = PROVIDER_NOT_SUPPORTED`

#### Scenario: Параметр не прошёл проверку

- **WHEN** вызывается `GET /api/v1/repositories?limit=abc`
- **THEN** ответ — `422` с `ApiError.code = VALIDATION_ERROR`
- **AND** `message` называет `limit` и причину, но не содержит `abc`
