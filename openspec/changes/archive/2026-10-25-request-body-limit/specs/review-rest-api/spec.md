## ADDED Requirements

### Requirement: Тело запроса ограничено по размеру

Тело запроса больше лимита SHALL отклоняться `413` с `code = REQUEST_BODY_TOO_LARGE` в формате Problem Details с `trace_id` и `X-Trace-ID`, до роутинга, разбора тела и сессии. По `Content-Length` отказ SHALL наступать без чтения тела, поток без длины SHALL NOT дочитываться дальше лимита. Лимит: `/api/v1/webhooks/` — `WEBHOOK_BODY_MAX_BYTES` (2 МиБ), остальное — `REQUEST_BODY_MAX_BYTES` (1 МиБ). Ответ SHALL нести `Connection: close`. Операции с телом SHALL объявлять `413` своей моделью ошибки.

#### Scenario: Большое тело ручки с сессией

- **WHEN** на `POST /api/v1/reviews` со своим `Origin` и без сессии приходит тело chunked больше лимита
- **THEN** ответ — `413` `REQUEST_BODY_TOO_LARGE`, а не `401` после чтения тела целиком

#### Scenario: Лимит вебхука не действует на другие ручки

- **WHEN** на `POST /api/v1/reviews` приходит тело больше 1 МиБ, которое приём вебхука бы принял
- **THEN** ответ — `413` `REQUEST_BODY_TOO_LARGE`, `detail` называет лимит 1 МиБ

#### Scenario: Отказ — Problem Details с trace id

- **WHEN** тело запроса больше лимита
- **THEN** ответ — `application/problem+json` с `code = REQUEST_BODY_TOO_LARGE`, `X-Trace-ID` равен `trace_id` тела, `detail` называет лимит

#### Scenario: Контракт объявляет 413

- **WHEN** строится схема API
- **THEN** у каждой операции с телом и у приёма вебхука ответ `413` описан моделью `RequestBodyTooLargeProblem`
