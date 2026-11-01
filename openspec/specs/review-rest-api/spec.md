# review-rest-api Specification

## Purpose
Определяет контракт REST API для фронтенда поверх модели данных пайплайна
ревью: постраничное чтение репозиториев, запросов на изменение и результатов
прогонов с находками, а также подключение репозитория; приём вебхуков и
работа воркера этим API не меняются.

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

Ответ SHALL содержать время последнего прогресса прогона
(`last_progress_at`) и этап сбоя (`failed_stage`); у прогона, который не
падал, этап сбоя SHALL быть `null`.

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

#### Scenario: Ответ содержит время последнего прогресса

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для идущего
  прогона
- **THEN** ответ содержит `last_progress_at` — время его последнего перехода,
  а `failed_stage` равен `null`

#### Scenario: Упавший прогон отдаёт этап сбоя

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` для прогона,
  упавшего на этапе `analysing`
- **THEN** ответ содержит `failed_stage: "analysing"` и причину сбоя

### Requirement: Находки прогона отдаются в стабильном порядке

`GET /api/v1/reviews/{review_run_id}` SHALL возвращать находки прогона в
одном и том же порядке при повторных вызовах.

#### Scenario: Повторные вызовы дают один порядок

- **WHEN** прогон с несколькими находками запрошен дважды подряд
- **THEN** порядок находок в обоих ответах совпадает

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

Каждое поле времени в JSON-ответах API и в данных событий общего потока SHALL отдаваться в UTC в форме `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. В схеме OpenAPI такое поле SHALL быть `format: date-time` с `pattern` этой формы.

#### Scenario: Время прогона

- **WHEN** прогон записан со временем `2026-10-11T13:00:00+03:00`
- **THEN** в ответе и в данных события `run.*` поле времени — `2026-10-11T10:00:00Z`

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

### Requirement: Дифф прогона описан заглушкой

`GET /api/v1/reviews/{review_run_id}/diff` SHALL быть описан в схеме с полной моделью ответа и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован. Сессию, формат id и видимость прогона он SHALL проверять так же, как реализованные ручки, — до ответа `501`.

#### Scenario: Дифф прогона

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/diff` с сессией для прогона, видимого пользователю
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Дифф без сессии

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/diff` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

### Requirement: Шаги прогона отдаются по порядку начала

`GET /api/v1/reviews/{review_run_id}/steps` SHALL отдавать шаги воркера в прогоне (`ReviewRunStepListResponse`) по порядку начала, при равном начале — по id. Каждый шаг SHALL содержать этап, статус, начало, длительность (`null`, пока шаг идёт), токены (`null`, если шаг модель не вызывал) и код ошибки (`null`, если шаг не падал) и SHALL NOT содержать запросов и ответов модели. Повтор этапа SHALL отдаваться отдельным шагом того же этапа. Ручка SHALL требовать сессию.

#### Scenario: Шаги прогона по порядку

- **WHEN** у прогона записаны шаги, и вызывается `GET /api/v1/reviews/{review_run_id}/steps` с сессией
- **THEN** ответ — `200`, `items` — шаги этого прогона по времени начала, упавшая попытка этапа и её повтор — два шага

#### Scenario: Прогон без шагов

- **WHEN** прогон существует, но воркер ещё не начал ни одного шага
- **THEN** ответ — `200` с пустым `items`

#### Scenario: Несуществующий прогон

- **WHEN** прогона с таким id нет
- **THEN** ответ — `404` `application/problem+json` с `code = REVIEW_NOT_FOUND`

#### Scenario: Шаги без сессии

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/steps` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Брошенный шаг без токенов

- **WHEN** прогон упал, а его шаг закрыт как брошенный (`error_code = REVIEW_RUN_STALE`)
- **THEN** у шага `status = failed` и `tokens_used = null`: сколько токенов он израсходовал, неизвестно

### Requirement: Список репозиториев, доступных для подключения

`GET /api/v1/repositories/available` SHALL требовать сессию и SHALL отдавать репозитории установок, которые GitHub отдаёт по токену пользователя сессии (`/user/installations…`), с `already_connected`. Не отданное GitHub SHALL NOT попадать в ответ. Приостановленная установка и установка с `403` (не лимит) или `404` SHALL пропускаться. Страниц SHALL быть не больше 50. Сбой GitHub, `403` лимита и неожиданное тело SHALL давать `502 SCM_UNAVAILABLE`.

#### Scenario: Список с отметкой подключённых

- **WHEN** по токену пользователя GitHub отдаёт репозитории `octo-org/api` и `octo-org/web`, `octo-org/api` уже зарегистрирован, и вызывается `GET /api/v1/repositories/available` с сессией
- **THEN** ответ — `200` с обоими репозиториями, у `octo-org/api` `already_connected = true`, у `octo-org/web` — `false`

#### Scenario: Репозиторий только с правом чтения показывается

- **WHEN** GitHub отдаёт `octo-org/web` с `permissions.admin = false` и `permissions.maintain = false`
- **THEN** `octo-org/web` есть в ответе

#### Scenario: Репозиторий без доступа не показывается

- **WHEN** `octo-org/api` есть в установке приложения, но у вошедшего пользователя нет к нему доступа, и GitHub не отдаёт его по токену пользователя
- **THEN** `octo-org/api` в ответе нет, ответ — `200`

#### Scenario: Чужой приватный репозиторий не показывается

- **WHEN** `octo-org/secret` приватный, в установке приложения, а вошедший пользователь к нему доступа не имеет
- **THEN** `octo-org/secret` в ответе нет: сервис показывает только то, что GitHub отдал по токену пользователя, и сам доступ не вычисляет

#### Scenario: Публичный репозиторий без явного права не показывается

- **WHEN** `octo-org/public` публичный, в установке приложения, а у вошедшего пользователя на нём нет роли (читать его может любой), и GitHub не отдаёт его по `/user/installations…`
- **THEN** `octo-org/public` в ответе нет, ответ — `200`: публичность репозитория видимости не даёт

#### Scenario: Приостановленная установка

- **WHEN** одна из установок пользователя приостановлена (`suspended_at` не `null`)
- **THEN** её репозитории не запрашиваются, ответ — `200` с репозиториями остальных установок

#### Scenario: Установка закрыта для токена

- **WHEN** GitHub отвечает `403` без признаков лимита на репозитории одной установки (например, организация требует SAML SSO)
- **THEN** установка пропускается, ответ — `200` с репозиториями остальных установок

#### Scenario: Лимит GitHub

- **WHEN** GitHub отвечает `403` с `x-ratelimit-remaining: 0`, с `retry-after` или с «rate limit» в `message` тела
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

#### Scenario: Ответ, который не кончается

- **WHEN** GitHub отдаёт полные страницы дольше 50 страниц
- **THEN** запрашивается не больше 50 страниц, список усечён

#### Scenario: Доступа нет ни к одному репозиторию

- **WHEN** GitHub не отдаёт пользователю ни одного репозитория установок
- **THEN** ответ — `200` с пустым `items`

#### Scenario: Список без сессии

- **WHEN** вызывается `GET /api/v1/repositories/available` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: GitHub недоступен при получении списка

- **WHEN** GitHub не ответил, ответил `429`, `5xx` или другой ошибкой, кроме `401`, или вернул тело без нужных полей, в том числе `null` в них
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, сессия не закрывается

### Requirement: Подключение репозитория

`POST /api/v1/repositories` SHALL требовать сессию и `Origin` сервиса и SHALL регистрировать репозиторий, найденный двумя запросами к GitHub (по id токеном пользователя, затем установка приложения), без обхода списка: `auto_review_enabled = true`, `full_name` и `default_branch` — из ответа GitHub, ответ — `201`. `installation_id` SHALL NOT храниться. Сбой GitHub SHALL давать `502 SCM_UNAVAILABLE` без записи.

#### Scenario: Подключение администратором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией пользователя, у которого на репозиторий `permissions.admin = true`, своим `Origin` и телом `{"provider": "github", "provider_id": "123456789"}`
- **THEN** ответ — `201` с репозиторием, `auto_review_enabled = true`
- **AND** репозиторий записан в `repositories`

#### Scenario: Два запроса к GitHub

- **WHEN** подключается репозиторий, видимый пользователю, с установленным приложением
- **THEN** к GitHub уходят ровно два запроса: репозиторий по id токеном пользователя и установка приложения на нём

### Requirement: Отказы подключения репозитория

Подключать репозиторий SHALL только пользователь с `permissions.admin` или `permissions.maintain`. Порядок: не `github`, не виден пользователю или публичный только с `pull`, приложение не установлено или приостановлено — `404 REPOSITORY_NOT_IN_INSTALLATION` (установка — после доступа); нет admin и maintain — `403 REPOSITORY_MANAGE_REQUIRED`; уже зарегистрирован — `409 REPOSITORY_ALREADY_CONNECTED`. Все `404` SHALL быть одинаковы; без прав SHALL NOT быть видно, подключён ли репозиторий.

#### Scenario: Подключение с ролью maintain

- **WHEN** у вошедшего пользователя на репозиторий `permissions.maintain = true` и `permissions.admin = false`
- **THEN** ответ — `201`, репозиторий записан

#### Scenario: Подключение без прав admin

- **WHEN** GitHub отдаёт репозиторий пользователю с `permissions.push = true` или `permissions.triage = true`, но с `permissions.admin = false` и `permissions.maintain = false`
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, записи нет

#### Scenario: Репозиторий только с правом чтения

- **WHEN** GitHub отдаёт публичный (`private = false`) репозиторий по id с `permissions.push`, `triage`, `maintain` и `admin` равными `false` — так он отдаёт любому чужой публичный репозиторий
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, тело совпадает с ответом на отсутствующий репозиторий, кроме `trace_id`, установка приложения не запрашивается, и ответ не зависит от того, установлено ли туда приложение

#### Scenario: Приватный репозиторий только с правом чтения

- **WHEN** GitHub отдаёт приватный (`private = true`) репозиторий по id с `permissions.push`, `triage`, `maintain` и `admin` равными `false` — у пользователя роль read, а приложение на репозитории установлено
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, записи нет: приватный репозиторий GitHub отдаёт только участнику, и отказ ему ничего не раскрывает

#### Scenario: Приватный репозиторий только с правом чтения без приложения

- **WHEN** у пользователя роль read на приватном репозитории, а приложение на нём не установлено или установка приостановлена
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`

#### Scenario: Подключение уже подключённого без прав

- **WHEN** репозиторий уже зарегистрирован, а у вошедшего пользователя нет ни admin, ни maintain на него
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, а не `409`

#### Scenario: Повторное подключение

- **WHEN** репозиторий уже зарегистрирован, и подключает пользователь с admin или maintain
- **THEN** ответ — `409` с `code = REPOSITORY_ALREADY_CONNECTED`

#### Scenario: Репозиторий не из установки

- **WHEN** `provider_id` нет среди репозиториев установок приложения
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`

#### Scenario: Невидимый репозиторий неотличим от отсутствующего

- **WHEN** репозиторий есть в установке, но GitHub не отдаёт его по токену пользователя
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, тело совпадает с ответом на отсутствующий репозиторий, кроме `trace_id`

#### Scenario: Приложение не установлено или установка приостановлена

- **WHEN** пользователь видит репозиторий, но приложение на нём не установлено или установка приостановлена
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, тело совпадает с ответом на невидимый репозиторий, кроме `trace_id`

#### Scenario: Установка не проверяется для чужого репозитория

- **WHEN** пользователь не видит репозиторий
- **THEN** установка приложения на нём не запрашивается, ответ — `404`

#### Scenario: GitHub недоступен при подключении

- **WHEN** GitHub не ответил или ответил ошибкой, кроме `401`
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, записи нет

#### Scenario: Подключение без Origin

- **WHEN** вызывается `POST /api/v1/repositories` без `Origin`
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`

#### Scenario: Подключение с пустым идентификатором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и пустым `provider_id`
- **THEN** ответ — `422` с `{"where": "body.provider_id", "reason": "длина строки должна быть не меньше 1"}`

### Requirement: Настройки репозитория описаны заглушкой

`GET` и `PUT /api/v1/repositories/{repository_id}/settings` SHALL быть описаны в схеме с полными моделями и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализованы. Ответ обеих — `RepositorySettingsResponse` с обязательными `auto_review_enabled`, `branch_filter` и `min_severity` из `FindingSeverity`. Обе SHALL требовать сессию, `PUT` — ещё `Origin` сервиса.

#### Scenario: Чтение настроек

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/settings` с сессией и UUID в пути
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Запись настроек

- **WHEN** вызывается `PUT /api/v1/repositories/{repository_id}/settings` с сессией, своим `Origin` и допустимым телом, в том числе с пустым `branch_filter`
- **THEN** ответ — `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Без сессии

- **WHEN** вызывается `GET` или `PUT` настроек без сессии (у `PUT` — со своим `Origin`)
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Запись без Origin

- **WHEN** вызывается `PUT` настроек без `Origin` или с чужим `Origin`, с сессией или без
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`, тело не разбирается

#### Scenario: Значения по умолчанию в контракте

- **WHEN** фронтенд читает описание `GET` настроек в `docs/openapi/openapi.json`
- **THEN** там указано: у репозитория без сохранённых настроек `auto_review_enabled = true`, `branch_filter = []` (все ветки), `min_severity = critical`

#### Scenario: Отказ по роли описан

- **WHEN** фронтенд читает `PUT` настроек в `docs/openapi/openapi.json`
- **THEN** ответ `403` допускает `ORIGIN_FORBIDDEN` и `REPOSITORY_MANAGE_REQUIRED`, а `auto_review_enabled` описан как та же колонка, что в `RepositoryResponse`

### Requirement: Тело записи настроек проверяется раньше 501

`PUT /api/v1/repositories/{repository_id}/settings` SHALL принимать `RepositorySettingsUpdate` — полный набор настроек: все три поля обязательны, лишние поля запрещены. Паттерн в `branch_filter` SHALL быть непустым после обрезки пробелов и не длиннее 200 символов, без повторов, паттернов SHALL быть не больше 50. Id репозитория и тело SHALL проверяться раньше ответа `501`.

#### Scenario: Неверный id

- **WHEN** вызывается `PUT` настроек с сессией, своим `Origin` и не-UUID в пути
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `path.repository_id`

#### Scenario: Неизвестная важность

- **WHEN** в теле `min_severity` вне `FindingSeverity`
- **THEN** ответ — `422` с ошибкой в `body.min_severity`

#### Scenario: Лишнее или пропущенное поле

- **WHEN** в теле есть поле вне модели или нет одного из трёх полей
- **THEN** ответ — `422` с `code = VALIDATION_ERROR`

#### Scenario: Пустой или длинный паттерн

- **WHEN** в `branch_filter` есть пустая строка или строка длиннее 200 символов
- **THEN** ответ — `422` с ошибкой в `body.branch_filter.<индекс>`

#### Scenario: Слишком много паттернов

- **WHEN** в `branch_filter` 51 паттерн
- **THEN** ответ — `422` с ошибкой в `body.branch_filter`

#### Scenario: Без сессии тело не проверяется

- **WHEN** вызывается `PUT` настроек со своим `Origin`, без сессии и с неверным телом
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Паттерн из пробелов

- **WHEN** в `branch_filter` есть паттерн `"   "`
- **THEN** ответ — `422` с причиной «длина строки должна быть не меньше 1» в `body.branch_filter.<индекс>`

#### Scenario: Повтор паттерна

- **WHEN** в `branch_filter` есть `main` и ` main ` (совпадают после обрезки пробелов)
- **THEN** ответ — `422` с причиной «паттерны веток повторяются» в `body.branch_filter`

### Requirement: Статус правил репозитория описан заглушкой

`GET /api/v1/repositories/{repository_id}/rules` SHALL быть описан в схеме с ответом `RulesStatusResponse` — объединением по `status` со значениями `RulesStatus` — и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован. Ручка SHALL требовать сессию и проверять id репозитория раньше `501`.

#### Scenario: Статус правил

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}/rules` с сессией и UUID в пути
- **THEN** ответ — `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Без сессии или с неверным id

- **WHEN** вызывается ручка без сессии или с сессией и не-UUID в пути
- **THEN** ответ — `401` с `code = UNAUTHENTICATED` или `422` с ошибкой в `path.repository_id`

#### Scenario: Варианты статуса в контракте

- **WHEN** фронтенд читает `RulesStatusResponse` в `docs/openapi/openapi.json`
- **THEN** у схемы дискриминатор `status` и пять вариантов: `loaded` (`branch`, `path`, `rules` из `RepoRule` с `name`, `patterns`, `excludes`, `checks`, `rejected` из `RejectedRule` с `name`, `reason`, `fetched_at` в UTC с `Z`), `empty` (`branch`, `path`, `rejected`, `fetched_at`), `absent`, `too_large`, `unavailable` (`branch`, `path`)

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

### Requirement: Типы и видимость событий общего потока описаны

Имя события `GET /api/v1/events` SHALL быть `<ресурс>.<действие>` в поле `event:`, данные — модель своего типа, перечисленная в `x-sse-events`. Клиент SHALL игнорировать незнакомые типы: новые добавляются без правки клиентов. Пользователь SHALL получать только видимые ему события; видимость задаёт правило типа события. Фильтров у потока нет.

#### Scenario: Сегодняшние типы

- **WHEN** фронтенд читает ответ потока и `components/schemas` в `docs/openapi/openapi.json`
- **THEN** в `x-sse-events` есть `run.created`, `run.status_changed` и `run.finished` со ссылкой на `ReviewRunListItem` и `ping` с пустым объектом, а `StreamEventType` перечисляет эти четыре имени

#### Scenario: Итоговый статус

- **WHEN** фронтенд читает описание потока
- **THEN** в нём сказано, что при переходе прогона в итоговый статус приходит только `run.finished`

#### Scenario: Видимость прогонов

- **WHEN** пользователь подключается к потоку
- **THEN** набор видимых репозиториев считается один раз тем же расчётом, что ответ `GET /repositories/available`, и до переподключения не пересчитывается; события `run.*` по прогонам остальных репозиториев не приходят

#### Scenario: Сбой провайдера при подключении

- **WHEN** GitHub не ответил или ответил ошибкой, пока считается набор видимых репозиториев
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, поток не открывается

#### Scenario: Незнакомый тип

- **WHEN** фронтенд читает описание потока
- **THEN** в нём сказано, что клиент игнорирует незнакомые типы событий

#### Scenario: Прогоны публичного репозитория без явного права

- **WHEN** у пользователя нет роли на публичном репозитории `octo-org/public`, к которому подключено приложение, и по `octo-org/public` идёт прогон
- **THEN** события `run.*` этого прогона пользователю не приходят: `octo-org/public` нет в ответе `GET /repositories/available`

### Requirement: Порядок версий строки в общем потоке событий описан

Описание `GET /api/v1/events` SHALL задавать правило для клиента: upsert строки прогона по `id`; побеждает больший `updated_at`, при равном — версия, пришедшая позже. Источник событий SHALL быть описан: уведомление PostgreSQL NOTIFY при коммите записи прогона; при обрыве источника поток закрывается. Истории нет: при подключении и после переподключения клиент перезапрашивает снимок REST.

#### Scenario: Правило в контракте

- **WHEN** фронтенд читает описание `GET /api/v1/events` в `docs/openapi/openapi.json`
- **THEN** в нём есть правило выбора версии по `updated_at`, источник событий — NOTIFY при коммите записи прогона и закрытие потока при обрыве источника, и перезапрос снимка `GET /reviews/{review_run_id}` или `GET /reviews` после события `open`

### Requirement: Истёкший токен пользователя обновляется

Ручки, обращающиеся к GitHub по токену пользователя (`GET /repositories/available`, `POST /repositories`, подключение к `GET /events`), SHALL обменивать истёкший access token по refresh token и сохранять новую пару. Сбой обмена или ожидание чужого дольше 15 секунд SHALL давать `502 SCM_UNAVAILABLE` без закрытия сессии, если до истечения access token меньше минуты; иначе запрос идёт прежним токеном. Два параллельных запроса одной сессии SHALL обменивать refresh token не больше одного раза.

#### Scenario: Истёкший токен обновляется

- **WHEN** access token сессии истёк, refresh token действует, и вызывается `GET /api/v1/repositories/available`
- **THEN** токен обменивается по refresh token, новая пара сохраняется, ответ — `200`

#### Scenario: Токен без истечения

- **WHEN** GitHub выдал при входе токен без `expires_in`
- **THEN** токен используется без обмена, сколько бы времени ни прошло

#### Scenario: GitHub недоступен при обмене

- **WHEN** access token истёк, а обмен refresh token не удался из-за сети, `5xx` или ошибки настройки приложения
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, сессия и токен остаются

#### Scenario: Сбой обмена при ещё действующем токене

- **WHEN** до истечения access token меньше 5 минут, но больше минуты, и обмен не удался из-за сбоя GitHub или чужая блокировка не отпущена за 15 секунд
- **THEN** запрос выполняется прежним access token, сессия и токен остаются

#### Scenario: Два запроса одной сессии с истёкшим токеном

- **WHEN** два запроса одной сессии одновременно видят истёкший access token
- **THEN** refresh token обменивается один раз, оба запроса используют новый токен

#### Scenario: Обмен при подключении к общему потоку

- **WHEN** access token сессии истёк, refresh token действует, и клиент подключается к `GET /api/v1/events`
- **THEN** токен обменивается до начала потока, видимые репозитории считаются новым токеном, поток открывается; если GitHub при обмене недоступен — `502` с `code = SCM_UNAVAILABLE`, поток не открывается, сессия остаётся

### Requirement: Непригодный токен пользователя закрывает сессию

Если у сессии нет токена пользователя, он не расшифровывается, refresh token истёк или отвергнут GitHub, или GitHub ответил `401` на токен, который не сменился и не обновился, ручки репозиториев и подключение к `GET /api/v1/events` SHALL удалять сессию вместе с токеном и отвечать `401 UNAUTHENTICATED` с удалением cookie `session`.

#### Scenario: Отвергнутый refresh token

- **WHEN** GitHub отвечает на обмен `bad_refresh_token`
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, cookie `session` удаляется, следующий запрос с ней получает `401`

#### Scenario: Отозванный токен

- **WHEN** GitHub отвечает `401` на запрос с токеном пользователя
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта

#### Scenario: Отказ на токен, заменённый параллельным обменом

- **WHEN** GitHub отвечает `401` на access token, а параллельный запрос той же сессии уже обменял refresh token
- **THEN** запрос повторяется один раз с новым токеном, сессия не закрывается

#### Scenario: Отказ на токен, истёкший посреди обхода

- **WHEN** GitHub отвечает `401` на access token, который не сменился, а refresh token действует
- **THEN** токен обменивается по refresh token, запрос повторяется один раз, сессия не закрывается

#### Scenario: Сессия, открытая до хранения токенов

- **WHEN** у сессии нет сохранённого токена
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта, пользователь входит заново

#### Scenario: Подключение к общему потоку с непригодным токеном

- **WHEN** клиент подключается к `GET /api/v1/events`, а токен сессии отозван, не обновляется или его нет
- **THEN** ответ — `401` с `code = UNAUTHENTICATED` до начала потока, сессия закрыта, cookie `session` удаляется

### Requirement: Общий поток событий реализован

`GET /api/v1/events` SHALL требовать сессию и отдавать `text/event-stream`, начиная со строки `retry: 3000`. Событие SHALL быть строкой `event: <тип>` и строкой `data:` с JSON в одну строку. Событие прогона SHALL приходить не позже чем через пару секунд после записи прогона, `ping` с `data: {}` — в тишине не реже раза в 15 секунд. Истории поток SHALL не отдавать. Сессия SHALL перепроверяться каждые 5 секунд; без неё поток SHALL закрываться.

#### Scenario: Смена статуса доходит сразу

- **WHEN** поток открыт, а воркер записал прогону видимого репозитория новый статус
- **THEN** клиент получает событие не позже чем через пару секунд после коммита записи, пока поток открыт

#### Scenario: Новый прогон

- **WHEN** после подключения создан прогон видимого репозитория и это его первое событие в соединении
- **THEN** событие — `run.created` с данными `ReviewRunListItem`

#### Scenario: Изменение без итогового статуса

- **WHEN** прогон, уже показанный или созданный до подключения, изменился, а статус не итоговый — в том числе изменился только прогресс
- **THEN** событие — `run.status_changed` с последней версией строки

#### Scenario: Итоговый статус

- **WHEN** прогон оказался в статусе `completed`, `failed` или `cancelled`, даже если это его первое событие
- **THEN** событие — только `run.finished`

#### Scenario: Истории нет

- **WHEN** клиент подключается, а прогоны менялись до подключения
- **THEN** событий по этим версиям нет, клиент берёт их снимком REST после `open`

#### Scenario: Равный updated_at

- **WHEN** два прогона записаны с одним `updated_at`, второй закоммичен после того, как поток увидел первый
- **THEN** приходят события обоих, при любом порядке их id

#### Scenario: Запоздавшая запись

- **WHEN** запись прогона закоммичена позже соседней, а её `updated_at` меньше уже увиденного, но не больше чем на 5 секунд
- **THEN** событие по ней приходит

#### Scenario: Тишина

- **WHEN** 15 секунд нет событий
- **THEN** приходит `event: ping` с `data: {}`

#### Scenario: Сессия истекла

- **WHEN** сессия закончилась, пока поток открыт
- **THEN** поток закрывается без события не позже чем через 5 секунд

#### Scenario: Без сессии

- **WHEN** вызывается `GET /api/v1/events` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, поток не открывается

#### Scenario: Контракт ответов

- **WHEN** фронтенд читает `GET /api/v1/events` в `docs/openapi/openapi.json`
- **THEN** у операции нет параметров, есть ответы `200` `text/event-stream`, `401` и `502`, ответа `501` нет; пути `/api/v1/reviews/events` нет, а `GET /api/v1/reviews/events` отвечает `422` проверки id прогона

#### Scenario: Трасса потока

- **WHEN** поток открыт часами, а клиент затем закрывает соединение
- **THEN** чтения базы потоком не пишут спанов SQL, а спан запроса закрывается без статуса ERROR

### Requirement: Список прогонов ревью

`GET /api/v1/reviews` SHALL требовать сессию и SHALL отвечать `200` со страницей прогонов: `items` — строки `ReviewRunListItem` с `MergeRequestSummary`, числом находок и обязательным `last_progress_at`, и `total` — число прогонов под фильтрами без учёта `limit` и `offset`. Порядок SHALL быть по `created_at` по убыванию, при равном — по `id` по убыванию. Строки страницы SHALL читаться числом запросов к базе, не зависящим от размера страницы.

#### Scenario: Страница не меняет total

- **WHEN** вызывается `GET /api/v1/reviews` с `limit=2&offset=1`, а под фильтры подходят четыре прогона
- **THEN** в `items` второй и третий прогоны по порядку списка, `total = 4`

#### Scenario: Список без сессии

- **WHEN** вызывается `GET /api/v1/reviews` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Контракт списка для фронтенда

- **WHEN** фронтенд читает `docs/openapi/openapi.json`
- **THEN** в `ReviewRunListResponse` есть обязательные `items` и `total`, в `ReviewRunListItem` — обязательный `last_progress_at` в UTC

### Requirement: Список прогонов показывает только видимые репозитории

`GET /api/v1/reviews` SHALL отдавать только прогоны репозиториев, к которым у пользователя есть доступ на GitHub, — то же правило, что у `GET /repositories/available` и потока `GET /events`. Набор видимых SHALL считаться токеном пользователя; в пределах TTL кэша SHALL браться из кэша (требование «Набор видимых репозиториев кэшируется»); сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`.

#### Scenario: Прогон невидимого репозитория

- **WHEN** вызывается `GET /api/v1/reviews` с сессией, а среди подключённых репозиториев есть такой, которого GitHub не отдаёт по токену пользователя
- **THEN** прогонов этого репозитория нет ни в `items`, ни в `total`

#### Scenario: Невидимый репозиторий в фильтре

- **WHEN** вызывается `GET /api/v1/reviews` с `repository_id` невидимого или несуществующего репозитория
- **THEN** ответ — `200` с пустым `items` и `total = 0`, а не `404`

#### Scenario: GitHub недоступен

- **WHEN** вызывается `GET /api/v1/reviews` с сессией, а GitHub не ответил на запрос списка репозиториев
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

### Requirement: Фильтры списка прогонов

Фильтры `GET /api/v1/reviews` SHALL складываться через «и». `q` SHALL искать подстроку без учёта регистра в заголовке, авторе или исходной ветке запроса на изменение; `%` и `_` в ней — обычные символы. `q` вида `42` или `#42` SHALL находить ещё и запрос на изменение с этим номером. `author` SHALL совпадать с логином точно. `created_from` SHALL входить в период, `created_to` — нет.

#### Scenario: Активные и статус вместе

- **WHEN** вызывается `GET /api/v1/reviews` с `active=true&status=queued&status=failed`
- **THEN** в `items` только прогоны в статусе `queued`

#### Scenario: Поиск по номеру

- **WHEN** вызывается `GET /api/v1/reviews` с `q=#42`
- **THEN** в `items` прогоны запроса на изменение с номером 42, но не с номером 4 или 420

#### Scenario: Знак процента в поиске

- **WHEN** вызывается `GET /api/v1/reviews` с `q=%`
- **THEN** в `items` только прогоны, у которых в заголовке, авторе или ветке есть символ `%`

### Requirement: Параметры списка прогонов проверяются раньше обращения к GitHub

`GET /api/v1/reviews` SHALL проверять параметры раньше, чем запросит у GitHub набор видимых репозиториев: `422` SHALL не стоить запроса к провайдеру. `status` и `repository_id` SHALL принимать несколько значений, `repository_id` — не больше 50. Пустые `q` и `author` SHALL означать «без фильтра». `created_from` и `created_to` SHALL быть временем ISO 8601 с зоной; `created_from` позже `created_to` — `422`. `limit` выше 200 SHALL приводиться к 200.

#### Scenario: Неверный параметр не доходит до GitHub

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и значением `status` вне `ReviewRunStatus`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR`, а к GitHub запроса не было

#### Scenario: Обратный диапазон дат

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from` позже `created_to`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `query.created_to`

#### Scenario: Время без зоны или не ISO 8601

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from` без зоны или `created_to` в виде unix-времени
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и причиной «ожидается время с зоной» или «ожидается время в ISO 8601»

#### Scenario: Без сессии параметры не проверяются

- **WHEN** вызывается `GET /api/v1/reviews` без сессии и с неверными параметрами
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Неверный id репозитория среди нескольких

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `repository_id` дважды, второй раз не UUID
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `query.repository_id.1`

#### Scenario: Больше 50 репозиториев

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и 51 значением `repository_id`
- **THEN** ответ — `422` с причиной «элементов должно быть не больше 50» в `query.repository_id`

#### Scenario: Время за краем календаря после сдвига зоны

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from=0001-01-01T00:00:00+01:00`
- **THEN** ответ — `422` с причиной «время вне допустимого диапазона» в `query.created_from`, а не `500`

#### Scenario: NUL в тексте фильтра

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и символом NUL в `q` или `author`
- **THEN** ответ — `422` с причиной «строка не должна содержать символ NUL», а не `500`

### Requirement: Прогон виден только тому, кто видит его репозиторий

`GET /api/v1/reviews/{review_run_id}`, `/steps` и `/diff` SHALL отдавать прогон, только если его репозиторий виден пользователю на GitHub, — то же правило, что у `GET /reviews` и `GET /events`. Прогон невидимого репозитория SHALL получать тот же ответ `404` с `code = REVIEW_NOT_FOUND` и тем же `detail`, что несуществующий. Прогон репозитория не с GitHub SHALL не быть виден.

#### Scenario: Прогон чужого репозитория

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` с сессией, а репозиторий прогона GitHub по токену пользователя не отдаёт
- **THEN** ответ — `404` с `code = REVIEW_NOT_FOUND` и тем же `detail`, что у несуществующего прогона

#### Scenario: Шаги и дифф чужого прогона

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/steps` или `/diff` для прогона невидимого репозитория
- **THEN** ответ — `404` с `code = REVIEW_NOT_FOUND`, для `/diff` — не `501`

### Requirement: Ответы GitHub не выдают, есть ли прогон

Ручки одного прогона SHALL проверять видимость — ответ GitHub или его кэш — при любом id — до чтения прогона, и для несуществующего тоже: иначе сбой GitHub, отозванный токен или время ответа выдавали бы, что id есть. Сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`, отозванный токен — `401` с `code = UNAUTHENTICATED` и закрытую сессию, одинаково для существующего и несуществующего id.

#### Scenario: Несуществующий прогон

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` с id, которого нет
- **THEN** ответ — `404` с `code = REVIEW_NOT_FOUND`, видимость проверена так же, как для существующего: запрос к GitHub был, если в кэше ответа нет

#### Scenario: GitHub недоступен при проверке

- **WHEN** GitHub не ответил на запрос списка репозиториев — прогон с этим id есть или его нет
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

#### Scenario: Токен отозван на GitHub

- **WHEN** GitHub отклонил токен пользователя при проверке видимости
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта

### Requirement: Репозиторий виден только тому, кто видит его на GitHub

`GET /api/v1/repositories` SHALL отдавать только репозитории, которые пользователь видит на GitHub, — то же правило, что у прогонов, `GET /reviews` и `GET /events`. `GET /api/v1/repositories/{repository_id}` и `/pull-requests` SHALL отвечать для невидимого репозитория тем же `404` с `code = REPOSITORY_NOT_FOUND` и тем же `detail`, что для несуществующего. Репозиторий не с GitHub SHALL не быть виден.

#### Scenario: Список без чужих репозиториев

- **WHEN** зарегистрированы два репозитория, а GitHub по токену пользователя отдаёт только один
- **THEN** `GET /api/v1/repositories` возвращает только его

#### Scenario: Чужой репозиторий по id

- **WHEN** вызывается `GET /api/v1/repositories/{repository_id}` или `/pull-requests` для репозитория, которого GitHub пользователю не отдаёт
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND` и тем же `detail`, что у несуществующего

### Requirement: Ответы GitHub не выдают, есть ли репозиторий

Ручки зарегистрированных репозиториев SHALL проверять видимость — ответ GitHub или его кэш — при любом id, до чтения базы. Сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`, отозванный токен — `401` с `code = UNAUTHENTICATED` и закрытую сессию. При `422` параметров запроса к GitHub SHALL NOT быть.

#### Scenario: GitHub недоступен

- **WHEN** вызывается `GET /api/v1/repositories` или `GET /api/v1/repositories/{repository_id}`, а GitHub не ответил
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

#### Scenario: Токен отозван на GitHub

- **WHEN** GitHub отклонил токен пользователя при проверке видимости
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта

#### Scenario: Неверный limit

- **WHEN** вызывается `GET /api/v1/repositories` с `limit=0`
- **THEN** ответ — `422`, запроса к GitHub не было

### Requirement: Набор видимых репозиториев кэшируется

Ответ GitHub о репозиториях, видимых пользователю, SHALL кэшироваться на `VISIBLE_REPOSITORIES_CACHE_TTL_SECONDS` (по умолчанию 30 с) для ручек видимости: `GET /reviews`, `GET /events`, ручек одного прогона и зарегистрированных репозиториев. Подключён ли репозиторий, SHALL читаться из базы на каждый запрос. `GET /repositories/available` SHALL всегда спрашивать GitHub и SHALL записывать свежий ответ в кэш; проверки подключения SHALL идти мимо кэша.

#### Scenario: Повтор в пределах TTL

- **WHEN** пользователь дважды за TTL запрашивает `GET /api/v1/reviews` или `GET /api/v1/repositories`
- **THEN** к GitHub за списком репозиториев был один запрос

#### Scenario: Подключённый репозиторий виден сразу

- **WHEN** пользователь подключает репозиторий и сразу запрашивает `GET /api/v1/repositories`
- **THEN** репозиторий в списке, без ожидания TTL

#### Scenario: Страница подключения без кэша

- **WHEN** пользователь запрашивает `GET /api/v1/repositories/available` в пределах TTL после другого запроса видимости
- **THEN** запрос к GitHub выполнен заново

#### Scenario: Приложение поставлено в пределах TTL

- **WHEN** кэш видимости заполнен, пользователь ставит приложение на новый репозиторий на GitHub, открывает `GET /api/v1/repositories/available` и подключает репозиторий
- **THEN** репозиторий сразу есть в `GET /api/v1/repositories`, а `GET /api/v1/repositories/{repository_id}` отвечает `200`

### Requirement: Кэш видимости не хранит токен и сбои

Ключ кэша видимых репозиториев SHALL зависеть от токена пользователя: другой токен — другой ключ. Сам токен SHALL NOT храниться ни в ключе, ни в значении. Сбои GitHub и отвергнутый токен SHALL NOT кэшироваться; сбой хранилища кэша SHALL быть промахом, а не ошибкой ответа.

#### Scenario: Сбой GitHub не запоминается

- **WHEN** GitHub не ответил, а следующий запрос пришёл в пределах TTL
- **THEN** следующий запрос снова идёт к GitHub

#### Scenario: Другой токен

- **WHEN** список видимых закэширован для одного токена, а запрос пришёл с другим
- **THEN** запрос идёт к GitHub, чужой список не отдаётся

### Requirement: Источник событий потока — NOTIFY при коммите

Запись прогона (создание и обновление) SHALL отправлять `pg_notify` в той же транзакции, поэтому уведомление SHALL уходить только при коммите и SHALL NOT уходить после `rollback`. API SHALL слушать канал одним соединением `LISTEN` на процесс и SHALL читать изменённые прогоны только по уведомлению, а не по таймеру: поток без изменений SHALL NOT читать прогоны из базы. События и их данные не меняются.

#### Scenario: Откат записи

- **WHEN** транзакция с записью прогона откатывается
- **THEN** уведомления нет, клиенты события не получают

#### Scenario: Тишина без чтений

- **WHEN** поток открыт, а прогоны не меняются
- **THEN** поток не читает прогоны из базы, приходит только `ping`

### Requirement: Обрыв источника событий закрывает поток

Если соединение `LISTEN` оборвалось, API SHALL закрыть открытые потоки: уведомления за время обрыва могли потеряться, а клиент после переподключения перезапрашивает снимок. Если источник недоступен при подключении, поток SHALL состоять из одной строки `retry: 3000`, чтобы EventSource переподключился.

#### Scenario: Обрыв LISTEN

- **WHEN** соединение `LISTEN` процесса API оборвалось, пока поток открыт
- **THEN** поток закрывается, а новая подписка после переподключения снова получает события

#### Scenario: Источник недоступен при подключении

- **WHEN** API не может открыть соединение `LISTEN`, а клиент подключается к `GET /api/v1/events`
- **THEN** ответ — `200` с телом `retry: 3000` и закрытием потока

### Requirement: Изменения одного коммита доходят до потока целиком

Если один коммит меняет больше прогонов, чем помещается в одно чтение потока, поток SHALL дочитать остаток сразу, не дожидаясь следующего уведомления. Строки окна перепроверки под курсором SHALL NOT вызывать повторного чтения без ожидания.

#### Scenario: Массовая смена статусов

- **WHEN** один коммит переводит в `failed` 250 прогонов, видимых пользователю, а поток открыт
- **THEN** клиент получает события по всем 250 прогонам без ожидания других изменений

#### Scenario: Полное окно перепроверки

- **WHEN** в окне перепроверки под курсором 200 и больше уже отправленных версий, а новых изменений нет
- **THEN** поток не перечитывает пачку снова, а ждёт уведомления

### Requirement: Подключение хранит канонический id репозитория

`provider_id` в теле `POST /api/v1/repositories` SHALL соответствовать `^[1-9][0-9]*$` (число из ASCII-цифр без ведущих нулей), иначе ответ — `422 VALIDATION_ERROR` без запроса к GitHub. Записанный `provider_id` и проверка «уже подключён» SHALL брать id из ответа GitHub, а не из запроса.

#### Scenario: Id с ведущим нулём

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и `"provider_id": "0101"`
- **THEN** ответ — `422` с `{"where": "body.provider_id", "reason": "недопустимое значение"}`, к GitHub запросов нет

#### Scenario: Не-ASCII цифры

- **WHEN** `provider_id` — `"١٠١"` или `"²"`
- **THEN** ответ — `422`, к GitHub запросов нет

#### Scenario: Записан id из ответа GitHub

- **WHEN** GitHub на `GET /repositories/{id}` отдаёт репозиторий с `id`, запись которого отличается от присланной
- **THEN** в `repositories.provider_id` записан `id` из ответа GitHub, а если репозиторий с этим id уже зарегистрирован — ответ `409 REPOSITORY_ALREADY_CONNECTED`

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

### Requirement: Признак права на подключение в списке доступных

Каждая строка `GET /api/v1/repositories/available` SHALL нести `can_connect`: `true`, если у пользователя на репозитории роль admin или maintain, иначе `false`. Правило SHALL совпадать с проверкой прав `POST /api/v1/repositories`, права SHALL браться из того же ответа GitHub, что и список. `POST /repositories` SHALL проверять права сам, независимо от поля.

#### Scenario: Роль admin или maintain

- **WHEN** GitHub отдаёт `octo-org/api` с `permissions.admin = true` или `permissions.maintain = true`
- **THEN** у `octo-org/api` в ответе `can_connect = true`, и `POST /repositories` с его `provider_id` его подключает (`201`) или отвечает `409`, если он уже подключён

#### Scenario: Роль ниже maintain

- **WHEN** GitHub отдаёт `octo-org/web` с ролью read, triage или write
- **THEN** у `octo-org/web` в ответе `can_connect = false`, и `POST /repositories` с его `provider_id` его не подключает: `403` с `code = REPOSITORY_MANAGE_REQUIRED`, а публичный только с правом чтения — `404`, как невидимый (такую строку GitHub в список не отдаёт: это ответ на прямой `POST`)

### Requirement: Инспекция находки описана заглушкой

`GET /api/v1/reviews/{review_run_id}/findings/{finding_id}/inspection` SHALL быть описан в схеме с ответом `FindingInspectionResponse` и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован.
Ручка SHALL требовать сессию.
Раньше `501` прогон SHALL проверяться по тому же правилу видимости, что у `GET /api/v1/reviews/{review_run_id}`, а находка - на принадлежность этому прогону.

#### Scenario: Находка видимого прогона

- **WHEN** вызывается инспекция находки, которая принадлежит видимому пользователю прогону
- **THEN** ответ - `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Находка другого прогона

- **WHEN** `finding_id` не принадлежит прогону `review_run_id`
- **THEN** ответ - `404` с `code = FINDING_NOT_FOUND`

#### Scenario: Невидимый прогон

- **WHEN** прогона `review_run_id` нет или он не виден пользователю
- **THEN** ответ - `404` с `code = REVIEW_NOT_FOUND`, находка не проверяется

#### Scenario: Неверный id находки

- **WHEN** в пути `finding_id` не UUID
- **THEN** ответ - `422` с ошибкой в `path.finding_id`

#### Scenario: Отказы инспекции в контракте

- **WHEN** фронтенд читает операцию инспекции в `docs/openapi/openapi.json`
- **THEN** ответ `404` допускает `REVIEW_NOT_FOUND` и `FINDING_NOT_FOUND`, а `FindingInspectionResponse` описывает итог второго прохода прогона, вердикт верификатора по находке с причиной и контекст модели по файлу находки, и `verdict` и `context` допускают `null`

### Requirement: Разбор правил песочницы

`POST /api/v1/rules/parse` SHALL разбирать `rules` тем же разбором, что ревью запроса на изменение применяет к `.review/rules.md`, без вызова модели, и SHALL отвечать `200` с `RulesParseResponse`.
Строки SHALL нумероваться с 1 и делиться только по `\n`.
Ручка SHALL требовать сессию и `Origin` сервиса.

#### Scenario: Принятое правило и отклонённый раздел

- **WHEN** в `rules` раздел `## Error Handling Standards` с паттерном и пунктами `1.` и `3.` и раздел `## Naming` без пунктов
- **THEN** ответ - `200`: правило со строкой заголовка в `line` и пунктами с `number` 1 и 2 (позиция, а не число автора), текстом и строкой; `Naming` - в `rejected` с причиной «нет проверок» и строкой заголовка

#### Scenario: Применимость к файлам

- **WHEN** у правила `pattern: app/**/*.py` и `exclude: **/tests/**`, а `paths` - `app/billing.py`, `app/tests/test_billing.py` и `lib/x.py`
- **THEN** `matched_paths` правила - `["app/billing.py"]`; без `paths` - пустой список

#### Scenario: Тот же разбор, что у ревью

- **WHEN** ревью разобрало бы тот же текст из `.review/rules.md`
- **THEN** правила и отклонённые разделы в ответе - те же и в том же порядке, включая разделы, съеденные незакрытым блоком кода

#### Scenario: Строки как в git

- **WHEN** строки текста разделены `\r\n`, а внутри строки есть `\x0c`
- **THEN** `\r\n` - одна граница строки, `\x0c` строку не делит, номера строк - как у редактора

#### Scenario: Пустой текст

- **WHEN** `rules` - пустая строка
- **THEN** ответ - `200` с пустыми `rules` и `rejected`: ревью шло бы по стандартным критериям

#### Scenario: Без Origin или без сессии

- **WHEN** вызывается `POST /api/v1/rules/parse` без `Origin` или с чужим `Origin` - или со своим `Origin`, но без сессии
- **THEN** ответ - `403` с `code = ORIGIN_FORBIDDEN` или `401` с `code = UNAUTHENTICATED`, тело не разбирается

### Requirement: Тело разбора правил проверяется

Тело `POST /api/v1/rules/parse` SHALL содержать `rules` не длиннее 32 768 символов, пустой допустим, и необязательный `paths` - не больше 100 путей файлов длиной от 1 до 1024 символов; текст с NUL и лишние поля SHALL отклоняться.
`rules` больше лимита, с которым ревью читает `.review/rules.md` (`RULES_MAX_BYTES`, в байтах UTF-8), SHALL отклоняться: такой файл ревью не разбирает.

#### Scenario: Неверное тело

- **WHEN** `rules` длиннее 32 768 символов или содержит NUL, в `paths` есть пустая строка, путей больше 100 или в теле есть лишнее поле
- **THEN** ответ - `422` с `code = VALIDATION_ERROR` и ошибкой в этом поле

#### Scenario: Больше лимита ревью

- **WHEN** `RULES_MAX_BYTES = 10`, а `rules` - шесть кириллических букв, 12 байт в UTF-8
- **THEN** ответ - `422` с `errors = [{"where": "body.rules", "reason": "размер больше 10 байт в UTF-8"}]` без самого текста; пять букв, 10 байт, - `200`

### Requirement: Запуск симуляции ревью

`POST /api/v1/simulations` SHALL записывать симуляцию в статусе `queued`, ставить её задачу в очередь и отвечать `202` с `SimulationResponse`.
Ручка SHALL требовать сессию и `Origin` сервиса; лимиты симуляций пользователя SHALL проверяться после тела, отказ - `429 RATE_LIMITED` с заголовком `Retry-After` в секундах.
Если задачу не удалось поставить в очередь, симуляция SHALL сразу переходить в `failed` с `error_code = QUEUE_UNAVAILABLE`, а ответ SHALL оставаться `202`.

#### Scenario: Запуск

- **WHEN** вызывается `POST /api/v1/simulations` с сессией, своим `Origin` и допустимым телом
- **THEN** ответ - `202`: `status = queued`, `findings`, `discarded` и `context` пустые, `expires_at` - через `SIMULATION_TTL_SECONDS` после `created_at`

#### Scenario: Незавершённая симуляция уже есть

- **WHEN** у пользователя есть симуляция не в `completed` и не в `failed`
- **THEN** ответ - `429` с `code = RATE_LIMITED` и `Retry-After: 5`, новая симуляция не записана

#### Scenario: Лимит за час

- **WHEN** у пользователя `SIMULATION_MAX_PER_HOUR` запусков за скользящий час
- **THEN** ответ - `429` с `code = RATE_LIMITED` и `Retry-After` - до выхода самого старого запуска из окна, не меньше 1

#### Scenario: Брокер недоступен

- **WHEN** задачу симуляции не удалось поставить в очередь
- **THEN** ответ - `202` с `status = failed` и `error_code = QUEUE_UNAVAILABLE`, и следующий запуск лимитом незавершённой симуляции не отклоняется

#### Scenario: Без Origin

- **WHEN** вызывается `POST /api/v1/simulations` без `Origin` или с чужим `Origin`
- **THEN** ответ - `403` с `code = ORIGIN_FORBIDDEN`, тело не разбирается

#### Scenario: Отказы в контракте

- **WHEN** фронтенд читает `POST /api/v1/simulations` в `docs/openapi/openapi.json`
- **THEN** успех - `202` с `SimulationResponse`, есть `429` с `code = RATE_LIMITED` и заголовком `Retry-After`, `501` нет

### Requirement: Чтение симуляции ревью

`GET /api/v1/simulations/{simulation_id}` SHALL отдавать автору симуляцию с `SimulationResponse`: статус, замечания с вердиктом верификатора, отброшенные замечания со стадией и причиной, контекст модели по файлам, `expires_at`.
Чужая, несуществующая и удалённая по сроку симуляция SHALL отвечать одним `404 SIMULATION_NOT_FOUND`.
Ход и итог клиент SHALL получать опросом этой ручки: в общий поток `GET /api/v1/events` симуляции не попадают.

#### Scenario: Завершённая симуляция

- **WHEN** автор читает симуляцию в статусе `completed`
- **THEN** ответ - `200` с `model`, `tokens_used`, `verification`, замечаниями, отброшенными и контекстом

#### Scenario: Упавшая симуляция

- **WHEN** автор читает симуляцию в статусе `failed`
- **THEN** ответ - `200` с `error_code`, пустыми `findings`, `discarded` и `context`

#### Scenario: Чужая симуляция

- **WHEN** пользователь читает симуляцию другого пользователя
- **THEN** ответ - `404` с `code = SIMULATION_NOT_FOUND`, тот же, что у несуществующей

#### Scenario: Итог - опросом

- **WHEN** фронтенд читает описание `POST /api/v1/simulations`
- **THEN** в нём сказано, что ход и итог читаются `GET /api/v1/simulations/{simulation_id}` опросом, пока статус не `completed` или `failed`, а в общий поток `GET /api/v1/events` симуляции не попадают

#### Scenario: Без сессии или с неверным id

- **WHEN** вызывается `GET /api/v1/simulations/{simulation_id}` без сессии или с сессией и не-UUID в пути
- **THEN** ответ - `401` с `code = UNAUTHENTICATED` или `422` с ошибкой в `path.simulation_id`

### Requirement: Вход симуляции проверяется

Тело `POST /api/v1/simulations` SHALL быть `SimulationRequest`: `source` - объединение по `kind`, и `rules` - текст `.review/rules.md`, пустой допустим.
Вариант `kind = code` SHALL содержать `file_path` длиной от 1 до 1024 символов и непустой `content`, вариант `kind = diff` - непустой `diff`; `content` и `diff` SHALL быть не длиннее 131 072 символов, `rules` - не длиннее 32 768; текст с NUL и лишние поля SHALL отклоняться.
Тело SHALL проверяться после `Origin` и сессии.

#### Scenario: Неизвестный вид входа

- **WHEN** в теле `source.kind` не `code` и не `diff`
- **THEN** ответ - `422` с `code = VALIDATION_ERROR` и ошибкой в `body.source`

#### Scenario: Код без пути

- **WHEN** в теле `source.kind = code`, а `file_path` нет
- **THEN** ответ - `422` с ошибкой в `body.source.code.file_path`

#### Scenario: Слишком длинный вход

- **WHEN** `source.content` длиннее 131 072 символов
- **THEN** ответ - `422` с ошибкой в `body.source.code.content`

#### Scenario: NUL в правилах

- **WHEN** `rules` содержит символ NUL
- **THEN** ответ - `422` с ошибкой в `body.rules`

#### Scenario: Без сессии тело не проверяется

- **WHEN** вызывается `POST /api/v1/simulations` со своим `Origin`, без сессии и с неверным телом
- **THEN** ответ - `401` с `code = UNAUTHENTICATED`

### Requirement: Вход, который ревью не возьмёт, отклоняется до записи

Вход, в котором после фильтра диффа ревью нечего ревьюить, и `rules` больше `RULES_MAX_BYTES` в байтах UTF-8 SHALL отклоняться `422` до записи симуляции и до проверки лимитов.

#### Scenario: Нечего ревьюить

- **WHEN** `source.diff` - текст без hunk'ов или дифф одного `uv.lock`, либо `source.kind = code` с `file_path = uv.lock`
- **THEN** ответ - `422` с ошибкой в `body.source.diff.diff` или `body.source.code.file_path`, симуляция не записана

#### Scenario: Правила больше лимита ревью

- **WHEN** `rules` больше `RULES_MAX_BYTES` в байтах UTF-8
- **THEN** ответ - `422` с ошибкой в `body.rules`, симуляция не записана
