## MODIFIED Requirements

### Requirement: Ручки чтения требуют сессию

Ручки чтения REST API — репозитории, запросы на изменение, прогоны ревью, их находки и общий поток событий `GET /api/v1/events` — SHALL требовать действительную сессию пользователя и без неё SHALL отвечать `401` с `code = UNAUTHENTICATED`.

#### Scenario: Запрос без сессии

- **WHEN** вызывается `GET /api/v1/repositories` без cookie сессии
- **THEN** ответ — `401` с `UNAUTHENTICATED`

#### Scenario: Запрос с сессией

- **WHEN** вызывается `GET /api/v1/repositories` с действительной cookie сессии
- **THEN** ответ — список репозиториев

### Requirement: Ошибки отдаются в формате Problem Details

Любая ошибка приложения SHALL отдаваться по RFC 9457 как `application/problem+json`: `type`, `title`, `status`, `detail`, `instance` и расширения `code` из закрытого списка и `trace_id`. Неизвестный путь — `404` `ROUTE_NOT_FOUND`, неразрешённый метод — `405` `METHOD_NOT_ALLOWED`, прочий отказ фреймворка — `HTTP_ERROR` с его статусом, сбой — `500` `INTERNAL_ERROR` без деталей. `detail` и `errors` SHALL NOT повторять переданное значение; `instance` — закодированный путь запроса без query.

#### Scenario: Несуществующий прогон

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для прогона, которого нет
- **THEN** ответ — `404` `application/problem+json` с `code = REVIEW_NOT_FOUND`

#### Scenario: Несуществующий репозиторий

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/pull-requests` для репозитория, которого нет
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND`

#### Scenario: Неподдержанный провайдер входа

- **WHEN** браузер запрашивает `GET /api/v1/auth/bitbucket/login`
- **THEN** ответ — `404` с `code = PROVIDER_NOT_SUPPORTED`

#### Scenario: Параметр не прошёл проверку

- **WHEN** вызывается `GET /api/v1/repositories?limit=abc`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR`
- **AND** `errors` содержит `{"where": "query.limit", "reason": "ожидается целое число"}`
- **AND** тело не содержит `abc`

#### Scenario: Неизвестный путь

- **WHEN** вызывается `GET /api/v1/nope`
- **THEN** ответ — `404` с `code = ROUTE_NOT_FOUND` и `instance = /api/v1/nope`

#### Scenario: Путь с закодированными символами

- **WHEN** вызывается `GET /api/v1/a%20b?q=1`
- **THEN** ответ — `404` с `code = ROUTE_NOT_FOUND` и `instance = /api/v1/a%20b`

#### Scenario: Прочий отказ фреймворка

- **WHEN** обработка запроса бросает `HTTPException` со статусом `429` и заголовком `Retry-After: 5`
- **THEN** ответ — `429` с `code = HTTP_ERROR` и тем же `Retry-After`
- **AND** текста исключения в теле нет

#### Scenario: Неразрешённый метод

- **WHEN** вызывается `DELETE /api/v1/webhooks/github`
- **THEN** ответ — `405` с `code = METHOD_NOT_ALLOWED` и заголовком `Allow: POST`

#### Scenario: Сбой сервера

- **WHEN** обработка запроса падает с необработанным исключением
- **THEN** ответ — `500` с `code = INTERNAL_ERROR`, в теле нет текста исключения и стектрейса
- **AND** исключение со стектрейсом пишется в лог сервера, как и раньше

### Requirement: Каждый ответ несёт trace id

Сервер SHALL выдавать каждому запросу собственный trace id — trace id серверного спана запроса, 32 строчных hex-символа (trace-id W3C Trace Context), не все нули — и отдавать его заголовком `X-Trace-ID` на любом ответе, включая поток SSE. Входящие `X-Trace-ID`, `X-Request-ID` и `traceparent` SHALL NOT приниматься: HTTP-запрос начинает новый трейс. В теле ошибки `trace_id` SHALL совпадать с этим заголовком.

#### Scenario: Идентификатор в ошибке

- **WHEN** запрос получает ответ с ошибкой
- **THEN** `trace_id` в теле равен заголовку `X-Trace-ID`

#### Scenario: Идентификатор — trace id спана запроса

- **WHEN** запрос получает ответ `404` или `500`
- **THEN** `X-Trace-ID` и `trace_id` в теле равны trace id серверного спана этого запроса, в том числе у `500`, который отдаётся снаружи всех middleware

#### Scenario: Трассировка выключена

- **WHEN** трассировка выключена (`OTEL_SDK_DISABLED=true`), и спана у запроса нет
- **THEN** `X-Trace-ID` — случайный trace id того же формата, у двух запросов разный

#### Scenario: Клиент прислал свой идентификатор

- **WHEN** запрос пришёл с заголовками `X-Trace-ID`, `X-Request-ID` или `traceparent`
- **THEN** в ответе один заголовок `X-Trace-ID`, и его значение — новый trace id сервера, а не присланный
- **AND** у серверного спана запроса нет родителя

#### Scenario: Успешный ответ

- **WHEN** вызывается `GET /health`
- **THEN** ответ — `200` с заголовком `X-Trace-ID` из 32 строчных hex-символов, и у двух запросов значения разные

#### Scenario: Поток статуса прогона

- **WHEN** вызывается `GET /api/v1/events` с сессией
- **THEN** ответ — `text/event-stream` с заголовком `X-Trace-ID`, равным trace id серверного спана запроса

### Requirement: Время в ответах — UTC с Z

Каждое поле времени в JSON-ответах API и в данных событий общего потока SHALL отдаваться в UTC в форме `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. В схеме OpenAPI такое поле SHALL быть `format: date-time` с `pattern` этой формы.

#### Scenario: Время прогона

- **WHEN** прогон записан со временем `2026-10-11T13:00:00+03:00`
- **THEN** в ответе и в данных события `run.*` поле времени — `2026-10-11T10:00:00Z`
