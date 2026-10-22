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

### Requirement: Прогон ревью отдаётся со сводкой запроса на изменение и публикациями

`GET /api/v1/reviews/{review_run_id}` SHALL отдавать вместе с прогоном число отсеянных находок, сводку его запроса на изменение с репозиторием и ссылкой у провайдера и опубликованные прогоном комментарии по времени публикации, затем по id. Ссылка SHALL быть `null` у провайдера, формат ссылок которого не поддержан.

#### Scenario: Прогон по запросу на изменение в GitHub

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для прогона по pull request `octo-org/api#42`
- **THEN** `merge_request.url` — `https://github.com/octo-org/api/pull/42`, а `merge_request.repository.full_name` — `octo-org/api`

#### Scenario: Провайдер без поддержанного формата ссылки

- **WHEN** запрос на изменение прогона живёт не в GitHub
- **THEN** `merge_request.url` — `null`

#### Scenario: Опубликованные комментарии в порядке публикации

- **WHEN** прогон опубликовал сводку, а затем комментарий к строке
- **THEN** `published_comments` содержит сначала сводку, затем комментарий к строке

#### Scenario: Прогон ещё ничего не опубликовал

- **WHEN** запрошен прогон без опубликованных комментариев
- **THEN** `published_comments` — пустой список

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

### Requirement: Находка отдаёт ссылку на правило, прогон - итог верификации

`GET /api/v1/reviews/{review_run_id}` SHALL отдавать у каждой находки поле `rule`: объект с именем правила (`name`), номером пункта (`check`) и текстом пункта (`check_text`) либо `null` для находки по стандартным критериям.
Ответ SHALL содержать у прогона итог верификации (`verification`, `null` до завершения анализа) и число отброшенных верификатором находок (`verifier_dropped_findings`).

#### Scenario: Находка по правилу

- **WHEN** запрошен прогон с находкой по пункту 2 правила `Error Handling Standards`
- **THEN** у находки `rule` содержит `name` `Error Handling Standards`, `check` 2 и текст пункта

#### Scenario: Находка по стандартным критериям

- **WHEN** запрошен прогон с находкой без ссылки на правило
- **THEN** у находки `rule` равен `null`

#### Scenario: Прогон с недоступной верификацией

- **WHEN** запрошен завершённый прогон, верификатор которого был недоступен
- **THEN** `verification` равен `unavailable`, а `verifier_dropped_findings` - 0

### Requirement: Список репозиториев, доступных для подключения

`GET /api/v1/repositories/available` SHALL требовать сессию и SHALL отдавать репозитории установок GitHub App, к которым у вошедшего пользователя есть доступ у провайдера (любая роль), с `already_connected` — есть ли репозиторий в `repositories`. Доступ к приватному репозиторию SHALL проверяться прямой проверкой участия пользователя в репозитории до запроса роли. Репозиторий без доступа SHALL NOT попадать в ответ, это не ошибка. Сбой GitHub SHALL давать `502 SCM_UNAVAILABLE`.

#### Scenario: Список с отметкой подключённых

- **WHEN** в установках приложения есть репозитории `octo-org/api` и `octo-org/web`, у вошедшего пользователя есть доступ к обоим, `octo-org/api` уже зарегистрирован, и вызывается `GET /api/v1/repositories/available` с сессией
- **THEN** ответ — `200` с обоими репозиториями, у `octo-org/api` `already_connected = true`, у `octo-org/web` — `false`

#### Scenario: Репозиторий только с правом чтения показывается

- **WHEN** у вошедшего пользователя роль read на `octo-org/web`
- **THEN** `octo-org/web` есть в ответе

#### Scenario: Репозиторий без доступа не показывается

- **WHEN** GitHub отвечает, что у вошедшего пользователя нет доступа к `octo-org/api` (роль `none` или `404`)
- **THEN** `octo-org/api` в ответе нет, ответ — `200`

#### Scenario: Чужой приватный репозиторий не показывается

- **WHEN** `octo-org/secret` приватный, проверка участия пользователя в нём отвечает `404`, а ручка прав отвечает что угодно
- **THEN** `octo-org/secret` в ответе нет, роль не запрашивается

#### Scenario: Доступа нет ни к одному репозиторию

- **WHEN** у вошедшего пользователя нет доступа ни к одному репозиторию установок
- **THEN** ответ — `200` с пустым `items`

#### Scenario: Список без сессии

- **WHEN** вызывается `GET /api/v1/repositories/available` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: GitHub недоступен при получении списка

- **WHEN** GitHub не ответил или ответил ошибкой на запрос списка установок, проверки участия или роли пользователя
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

### Requirement: Подключение репозитория

`POST /api/v1/repositories` SHALL требовать сессию и `Origin` сервиса и SHALL регистрировать репозиторий установки GitHub App: `auto_review_enabled = true`, `full_name` и `default_branch` — из данных установки, ответ — `201` с `RepositoryResponse`. `installation_id` SHALL NOT храниться: его даёт список установок приложения. Сбой GitHub SHALL давать `502` `SCM_UNAVAILABLE` без записи.

#### Scenario: Подключение администратором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией пользователя с ролью admin, своим `Origin` и телом `{"provider": "github", "provider_id": "123456789"}` репозитория установки
- **THEN** ответ — `201` с репозиторием, `auto_review_enabled = true`
- **AND** репозиторий записан в `repositories`

### Requirement: Отказы подключения репозитория

Подключать репозиторий SHALL только пользователь с ролью admin или maintain у провайдера. Проверки SHALL идти по порядку: не `github` или нет в установках — `404 REPOSITORY_NOT_IN_INSTALLATION`; нет роли admin или maintain — `403 REPOSITORY_MANAGE_REQUIRED`; уже зарегистрирован, в том числе параллельным запросом, — `409 REPOSITORY_ALREADY_CONNECTED`. Пользователь без прав SHALL NOT узнавать из отказа, подключён ли репозиторий.

#### Scenario: Подключение с ролью maintain

- **WHEN** у вошедшего пользователя роль maintain на репозиторий установки
- **THEN** ответ — `201`, репозиторий записан

#### Scenario: Подключение без прав admin

- **WHEN** у вошедшего пользователя нет ни роли admin, ни maintain: роль write, triage, read или пользовательская роль, нет доступа или GitHub отвечает, что он не коллаборатор
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, записи нет

#### Scenario: Подключение уже подключённого без прав

- **WHEN** репозиторий уже зарегистрирован, а у вошедшего пользователя нет роли admin или maintain на него
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, а не `409`

#### Scenario: Повторное подключение

- **WHEN** репозиторий уже зарегистрирован, и подключает пользователь с ролью admin или maintain
- **THEN** ответ — `409` с `code = REPOSITORY_ALREADY_CONNECTED`

#### Scenario: Репозиторий не из установки

- **WHEN** `provider_id` нет среди репозиториев установок приложения
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`

#### Scenario: GitHub недоступен при подключении

- **WHEN** GitHub не ответил или ответил ошибкой
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, записи нет

#### Scenario: Подключение без Origin

- **WHEN** вызывается `POST /api/v1/repositories` без `Origin`
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`

#### Scenario: Подключение с пустым идентификатором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и пустым `provider_id`
- **THEN** ответ — `422` с `{"where": "body.provider_id", "reason": "длина строки должна быть не меньше 1"}`
