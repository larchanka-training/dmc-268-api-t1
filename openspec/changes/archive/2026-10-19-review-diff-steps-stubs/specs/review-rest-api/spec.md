## ADDED Requirements

### Requirement: Дифф и шаги прогона описаны заглушками

`GET /api/v1/reviews/{review_run_id}/diff` и `GET /api/v1/reviews/{review_run_id}/steps` SHALL быть описаны в схеме с полными моделями ответа и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализованы. Сессию и формат id прогона они SHALL проверять так же, как реализованные ручки.

#### Scenario: Дифф прогона

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/diff` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Шаги прогона

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/steps` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Без сессии

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/diff` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`
