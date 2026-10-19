# review-rest-api Specification

## Purpose
Определяет read-контракт REST API для фронтенда поверх уже существующей
модели данных пайплайна ревью: список репозиториев, список запросов на
изменение репозитория и результат прогона ревью с находками — постранично,
без изменения того, что пишут вебхук и воркер.

## Requirements

### Requirement: Список репозиториев постранично

Система SHALL предоставлять `GET /api/v1/repositories`, возвращающий
постраничный список зарегистрированных репозиториев. Запрос SHALL
принимать `limit` и `offset`; при отсутствии `limit` SHALL применяться
значение по умолчанию, а значение выше зафиксированного потолка SHALL быть
приведено к этому потолку, а не отклонено ошибкой.

#### Scenario: Список репозиториев возвращается постранично

- **WHEN** вызывается `GET /api/v1/repositories` без параметров
- **THEN** ответ содержит репозитории числом не больше значения по
  умолчанию

#### Scenario: `limit` выше потолка не отклоняется

- **WHEN** вызывается `GET /api/v1/repositories` с `limit` больше
  зафиксированного потолка
- **THEN** ответ успешен и содержит не больше репозиториев, чем позволяет
  потолок

### Requirement: Список запросов на изменение репозитория

Система SHALL предоставлять `GET
/api/v1/repositories/{repository_id}/pull-requests`, возвращающий
постраничный список запросов на изменение указанного репозитория. Запрос к
несуществующему репозиторию SHALL быть отклонён кодом 404.

#### Scenario: Список запросов на изменение возвращается для существующего репозитория

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/pull-requests`
  для зарегистрированного репозитория
- **THEN** ответ содержит запросы на изменение этого репозитория

#### Scenario: Несуществующий репозиторий — 404

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/pull-requests`
  для `repository_id`, которого нет в системе
- **THEN** ответ — 404, и список запросов на изменение не возвращается

### Requirement: Результат прогона ревью с находками

Система SHALL предоставлять `GET /api/v1/reviews/{review_run_id}`,
возвращающий прогон ревью вместе со всеми его находками в одном ответе.
Запрос к несуществующему прогону SHALL быть отклонён кодом 404.

#### Scenario: Существующий прогон возвращается с находками

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для
  существующего прогона
- **THEN** ответ содержит поля прогона (включая статус, модель, число
  токенов, если прогон их накопил) и список всех его находок

#### Scenario: Прогон без находок возвращает пустой список, а не ошибку

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для прогона,
  который не сформировал ни одной находки
- **THEN** ответ успешен, а список находок пуст

#### Scenario: Несуществующий прогон — 404

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для
  `review_run_id`, которого нет в системе
- **THEN** ответ — 404

### Requirement: Находки прогона отдаются в стабильном порядке

`GET /api/v1/reviews/{review_run_id}` SHALL возвращать находки прогона в
одном и том же порядке при повторных вызовах.

#### Scenario: Повторные вызовы дают один порядок

- **WHEN** прогон с несколькими находками запрошен дважды подряд
- **THEN** порядок находок в обоих ответах совпадает

### Requirement: Ручки чтения требуют сессию

Ручки чтения REST API — репозитории, запросы на изменение, прогоны ревью, их находки и поток статуса прогона — SHALL требовать действительную сессию пользователя и без неё SHALL отвечать `401` с `code = UNAUTHENTICATED`.

#### Scenario: Запрос без сессии

- **WHEN** вызывается `GET /api/v1/repositories` без cookie сессии
- **THEN** ответ — `401` с `UNAUTHENTICATED`

#### Scenario: Запрос с сессией

- **WHEN** вызывается `GET /api/v1/repositories` с действительной cookie сессии
- **THEN** ответ — список репозиториев

### Requirement: Ошибки отдаются в формате Problem Details

Любая ошибка приложения SHALL отдаваться по RFC 9457 как `application/problem+json`: `type`, `title`, `status`, `detail`, `instance` и расширения `code` из закрытого списка и `trace_id`. Неизвестный путь — `404` `ROUTE_NOT_FOUND`, неразрешённый метод — `405` `METHOD_NOT_ALLOWED`, прочий отказ фреймворка — `HTTP_ERROR` с его статусом, сбой — `500` `INTERNAL_ERROR` без деталей. `detail` и `errors` SHALL NOT повторять переданное значение; `instance` — закодированный путь запроса без query.

#### Scenario: Несуществующий прогон

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` или `GET /api/v1/reviews/{review_run_id}/events` для прогона, которого нет
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

Сервер SHALL выдавать каждому запросу собственный trace id — 32 строчных hex-символа в формате trace-id W3C Trace Context, не все нули — и отдавать его заголовком `X-Trace-ID` на любом ответе, включая поток SSE. Входящие `X-Trace-ID`, `X-Request-ID` и `traceparent` SHALL NOT приниматься. В теле ошибки `trace_id` SHALL совпадать с этим заголовком.

#### Scenario: Идентификатор в ошибке

- **WHEN** запрос получает ответ с ошибкой
- **THEN** `trace_id` в теле равен заголовку `X-Trace-ID`

#### Scenario: Клиент прислал свой идентификатор

- **WHEN** запрос пришёл с заголовками `X-Trace-ID`, `X-Request-ID` или `traceparent`
- **THEN** в ответе один заголовок `X-Trace-ID`, и его значение — новый trace id сервера, а не присланный

#### Scenario: Успешный ответ

- **WHEN** вызывается `GET /health`
- **THEN** ответ — `200` с заголовком `X-Trace-ID` из 32 строчных hex-символов, и у двух запросов значения разные

#### Scenario: Поток статуса прогона

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/events` с сессией для существующего прогона
- **THEN** ответ — `text/event-stream` с заголовком `X-Trace-ID`

### Requirement: Сбой сервера оставляет строку лога с trace id

На необработанное исключение сервер SHALL писать в лог одну строку уровня error с `trace_id`, методом и путём запроса без query. Текста исключения и стектрейса в этой строке SHALL NOT быть: стектрейс пишет сервер приложений, как и раньше.

#### Scenario: Поиск сбоя по trace id

- **WHEN** обработка `GET /api/v1/x?q=секрет` падает с необработанным исключением
- **THEN** в логе есть одна строка уровня error с `trace_id` из тела ответа, `GET` и `/api/v1/x`
- **AND** в ней нет query, текста исключения и стектрейса

### Requirement: Схема API строится из кода

`GET /openapi.json` SHALL отдавать схему OpenAPI, построенную из роутов и моделей приложения, а `/docs` — Swagger UI по ней. Каждый ответ ошибки в схеме SHALL быть `application/problem+json` с моделью своего кода, `422` — `ValidationProblem`. `docs/openapi/openapi.json` SHALL совпадать со схемой из кода: устаревший файл роняет CI.

#### Scenario: Схема API

- **WHEN** вызывается `GET /openapi.json`
- **THEN** ответ — `200`, и его JSON равен `docs/openapi/openapi.json`

#### Scenario: Просмотр документации

- **WHEN** браузер открывает `/docs`
- **THEN** ответ — `200` HTML Swagger UI, который читает `/openapi.json`

#### Scenario: Экспорт устарел

- **WHEN** ручка или модель ответа изменилась, а `docs/openapi/openapi.json` не обновлён
- **THEN** `uv run python scripts/export_openapi.py --check` завершается с кодом `1`

### Requirement: Запланированная ручка отвечает 501

Ручка, которая описана в схеме до реализации, SHALL быть в коде заглушкой с полной схемой запроса и ответов и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`. Сессию, `Origin` и тело запроса она SHALL проверять так же, как реализованная.

#### Scenario: Ручной запуск ревью

- **WHEN** вызывается `POST /api/v1/reviews` с сессией, своим `Origin` и корректным телом
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Ручной запуск без сессии

- **WHEN** вызывается `POST /api/v1/reviews` со своим `Origin`, но без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

### Requirement: Время в ответах — UTC с Z

Каждое поле времени в JSON-ответах API и в данных событий потока статуса SHALL отдаваться в UTC в форме `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. В схеме OpenAPI такое поле SHALL быть `format: date-time` с `pattern` этой формы.

#### Scenario: Время прогона

- **WHEN** прогон записан со временем `2026-10-11T13:00:00+03:00`
- **THEN** в ответе и в событии `status` поле времени — `2026-10-11T10:00:00Z`

### Requirement: Offset списков в пределах bigint

Параметр `offset` списков SHALL быть целым от `0` до `9223372036854775807` (максимум bigint). Значение вне диапазона или слишком длинное число SHALL получать `422` с `code = VALIDATION_ERROR`, а не `500`, и без самого значения в теле. Верхней границы у `limit` нет: значение выше потолка приводится к потолку.

#### Scenario: Offset больше bigint

- **WHEN** вызывается `GET /api/v1/repositories?offset=9223372036854775808`
- **THEN** ответ — `422` с `VALIDATION_ERROR` и `{"where": "query.offset", "reason": "должно быть не больше 9223372036854775807"}`

### Requirement: Тело запроса проверяется строго

Тело изменяющего запроса SHALL приниматься только с полями из схемы в camelCase и без приведения типов: число строкой, строка числом, snake_case и лишнее поле получают `422` `VALIDATION_ERROR`. Строки и числа SHALL укладываться в границы колонок. Имя лишнего поля SHALL NOT попадать в ответ: `where` указывает на объект, `reason` — «лишнее поле».

#### Scenario: Число строкой

- **WHEN** `POST /api/v1/reviews` с сессией и своим `Origin` пришёл с `"pullRequestNumber": "5"`
- **THEN** ответ — `422` с `{"where": "body.pullRequestNumber", "reason": "ожидается целое число"}`

#### Scenario: Лишнее поле

- **WHEN** тело `POST /api/v1/reviews` содержит поле `<img src=x onerror=alert(1)>`
- **THEN** ответ — `422` с `{"where": "body", "reason": "лишнее поле"}`, имени поля в ответе нет

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
