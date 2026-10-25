## ADDED Requirements

### Requirement: Тело запроса ограничено по размеру

Тело любого запроса API больше `REQUEST_BODY_MAX_BYTES` SHALL отклоняться `413` с `code = REQUEST_BODY_TOO_LARGE` в формате Problem Details с `trace_id` и заголовком `X-Trace-ID`, до роутинга, разбора тела и проверки сессии. По `Content-Length` отказ SHALL наступать без чтения тела, поток без длины SHALL NOT дочитываться дальше лимита. Ответ SHALL нести `Connection: close`. Операции с телом SHALL объявлять `413` в схеме своей моделью ошибки.

#### Scenario: Большое тело ручки с сессией

- **WHEN** на `POST /api/v1/reviews` со своим `Origin` и без сессии приходит тело chunked больше лимита
- **THEN** ответ — `413` `REQUEST_BODY_TOO_LARGE`, а не `401` после чтения тела целиком

#### Scenario: Отказ — Problem Details с trace id

- **WHEN** тело запроса больше лимита
- **THEN** ответ — `application/problem+json` с `code = REQUEST_BODY_TOO_LARGE`, `X-Trace-ID` равен `trace_id` тела, `detail` называет лимит

#### Scenario: Контракт объявляет 413

- **WHEN** строится схема API
- **THEN** у каждой операции с телом и у приёма вебхука ответ `413` описан моделью `RequestBodyTooLargeProblem`
