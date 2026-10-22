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

`GET /api/v1/repositories/available` SHALL требовать сессию и SHALL отдавать репозитории установок GitHub App, которые GitHub отдаёт по токену пользователя сессии (`/user/installations…`), с `already_connected` — есть ли репозиторий в `repositories`. Репозиторий, которого GitHub пользователю не отдал, SHALL NOT попадать в ответ. Приостановленная установка SHALL пропускаться. Сбой GitHub и неожиданное тело SHALL давать `502 SCM_UNAVAILABLE`.

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

#### Scenario: Приостановленная установка

- **WHEN** одна из установок пользователя приостановлена (`suspended_at` не `null`)
- **THEN** её репозитории не запрашиваются, ответ — `200` с репозиториями остальных установок

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

`POST /api/v1/repositories` SHALL требовать сессию и `Origin` сервиса и SHALL регистрировать репозиторий из списка, который GitHub отдаёт по токену пользователя сессии: `auto_review_enabled = true`, `full_name` и `default_branch` — из ответа GitHub, ответ — `201` с `RepositoryResponse`. `installation_id` SHALL NOT храниться. Сбой GitHub SHALL давать `502` `SCM_UNAVAILABLE` без записи.

#### Scenario: Подключение администратором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией пользователя, у которого на репозиторий `permissions.admin = true`, своим `Origin` и телом `{"provider": "github", "provider_id": "123456789"}`
- **THEN** ответ — `201` с репозиторием, `auto_review_enabled = true`
- **AND** репозиторий записан в `repositories`

### Requirement: Отказы подключения репозитория

Подключать репозиторий SHALL только пользователь с `permissions.admin` или `permissions.maintain` в ответе GitHub. Порядок проверок: не `github` или нет в списке пользователя — `404 REPOSITORY_NOT_IN_INSTALLATION`; нет admin и maintain — `403 REPOSITORY_MANAGE_REQUIRED`; уже зарегистрирован, в том числе параллельно, — `409 REPOSITORY_ALREADY_CONNECTED`. Отсутствующий и невидимый репозиторий SHALL получать одинаковый ответ; пользователь без прав SHALL NOT узнавать, подключён ли репозиторий.

#### Scenario: Подключение с ролью maintain

- **WHEN** у вошедшего пользователя на репозиторий `permissions.maintain = true` и `permissions.admin = false`
- **THEN** ответ — `201`, репозиторий записан

#### Scenario: Подключение без прав admin

- **WHEN** GitHub отдаёт репозиторий пользователю с `permissions.admin = false` и `permissions.maintain = false`
- **THEN** ответ — `403` с `code = REPOSITORY_MANAGE_REQUIRED`, записи нет

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

#### Scenario: GitHub недоступен при подключении

- **WHEN** GitHub не ответил или ответил ошибкой, кроме `401`
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, записи нет

#### Scenario: Подключение без Origin

- **WHEN** вызывается `POST /api/v1/repositories` без `Origin`
- **THEN** ответ — `403` с `code = ORIGIN_FORBIDDEN`

#### Scenario: Подключение с пустым идентификатором

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и пустым `provider_id`
- **THEN** ответ — `422` с `{"where": "body.provider_id", "reason": "длина строки должна быть не меньше 1"}`

### Requirement: Список прогонов описан заглушкой

`GET /api/v1/reviews` SHALL быть описан в схеме с полной моделью ответа и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован; сессию SHALL требовать. Ответ SHALL содержать `items` — строки `ReviewRunListItem` с `MergeRequestSummary` и числом находок — и `total` — число прогонов под фильтрами без учёта `limit` и `offset`.

#### Scenario: Список прогонов

- **WHEN** вызывается `GET /api/v1/reviews` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Список без сессии

- **WHEN** вызывается `GET /api/v1/reviews` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Контракт списка для фронтенда

- **WHEN** фронтенд читает `docs/openapi/openapi.json`
- **THEN** в `ReviewRunListResponse` есть обязательные `items` и `total`

### Requirement: Параметры списка прогонов проверяются раньше 501

`GET /api/v1/reviews` SHALL проверять параметры фильтров и пагинации раньше ответа `501`. У `q` и `author` пустая строка SHALL означать «без фильтра». `created_from` и `created_to` SHALL принимать только строку ISO 8601 с зоной, любой. `created_from` позже `created_to` SHALL отклоняться кодом `422` вместе с остальными ошибками параметров.

#### Scenario: Обратный диапазон дат

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from` позже `created_to`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `query.created_to`

#### Scenario: Время без зоны или не ISO 8601

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и `created_from` без зоны или `created_to` в виде unix-времени
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и причиной «ожидается время с зоной» или «ожидается время в ISO 8601»

#### Scenario: Статус не из списка

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и значением `status` вне `ReviewRunStatus`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR`

#### Scenario: Все фильтры сразу

- **WHEN** вызывается `GET /api/v1/reviews` с сессией и допустимыми `status` (несколько значений), `active`, `repository_id`, `created_from`, `created_to`, `limit`, `offset`, пустыми `q` и `author` и временем с зоной `+03:00`
- **THEN** ответ — `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Без сессии параметры не проверяются

- **WHEN** вызывается `GET /api/v1/reviews` без сессии и с неверными параметрами
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

### Requirement: Поток изменений прогонов описан заглушкой

`GET /api/v1/reviews/events` SHALL быть описан в схеме как `text/event-stream` и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован; сессию SHALL требовать. Данные событий `run.*` SHALL быть `ReviewRunListItem`, имена событий — enum `ReviewRunsStreamEventType`. При итоговом статусе SHALL приходить только `run.finished`. Путь SHALL NOT попадать в `GET /api/v1/reviews/{review_run_id}`.

#### Scenario: Поток изменений

- **WHEN** вызывается `GET /api/v1/reviews/events` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`, а не `422` проверки id прогона

#### Scenario: Поток без сессии

- **WHEN** вызывается `GET /api/v1/reviews/events` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Данные событий — строка списка

- **WHEN** фронтенд читает ответ потока и `components/schemas` в `docs/openapi/openapi.json`
- **THEN** `run.created`, `run.status_changed` и `run.finished` ссылаются на `ReviewRunListItem`, а `ReviewRunsStreamEventType` перечисляет `run.created`, `run.status_changed`, `run.finished` и `ping`

#### Scenario: Прогон по id

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` с id существующего прогона
- **THEN** ответ — `200` с этим прогоном, как до появления потока

### Requirement: Порядок версий строки в потоке прогонов описан

Описание `GET /api/v1/reviews/events` SHALL задавать правило для клиента: upsert строки по `id`; побеждает больший `updated_at`, при равном — версия, пришедшая позже; снимок `GET /api/v1/reviews` запрашивается после события `open`. Поток SHALL NOT учитывать фильтры списка: при активных фильтрах или не на первой странице клиент на событие перезапрашивает страницу.

#### Scenario: Правило в контракте

- **WHEN** фронтенд читает описание `GET /api/v1/reviews/events` в `docs/openapi/openapi.json`
- **THEN** в нём есть правило выбора версии по `updated_at`, порядок «`open`, затем снимок» и перезапрос страницы при фильтрах

### Requirement: Истёкший токен пользователя обновляется

Ручки, обращающиеся к GitHub по токену пользователя (`GET /api/v1/repositories/available`, `POST /api/v1/repositories`), SHALL обменивать истёкший access token по refresh token и сохранять новую пару. Сбой GitHub при обмене SHALL давать `502 SCM_UNAVAILABLE` без закрытия сессии. Два параллельных запроса одной сессии SHALL обменивать refresh token не больше одного раза.

#### Scenario: Истёкший токен обновляется

- **WHEN** access token сессии истёк, refresh token действует, и вызывается `GET /api/v1/repositories/available`
- **THEN** токен обменивается по refresh token, новая пара сохраняется, ответ — `200`

#### Scenario: Токен без истечения

- **WHEN** GitHub выдал при входе токен без `expires_in`
- **THEN** токен используется без обмена, сколько бы времени ни прошло

#### Scenario: GitHub недоступен при обмене

- **WHEN** обмен refresh token не удался из-за сети, `5xx` или ошибки настройки приложения
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`, сессия и токен остаются

#### Scenario: Два запроса одной сессии с истёкшим токеном

- **WHEN** два запроса одной сессии одновременно видят истёкший access token
- **THEN** refresh token обменивается один раз, оба запроса используют новый токен

### Requirement: Непригодный токен пользователя закрывает сессию

Если у сессии нет токена пользователя, он не расшифровывается, refresh token истёк или отвергнут GitHub, или GitHub ответил `401` на токен, ручки репозиториев SHALL удалять сессию вместе с токеном и отвечать `401 UNAUTHENTICATED` с удалением cookie `session`.

#### Scenario: Отвергнутый refresh token

- **WHEN** GitHub отвечает на обмен `bad_refresh_token`
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, cookie `session` удаляется, следующий запрос с ней получает `401`

#### Scenario: Отозванный токен

- **WHEN** GitHub отвечает `401` на запрос с токеном пользователя
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта

#### Scenario: Сессия, открытая до хранения токенов

- **WHEN** у сессии нет сохранённого токена
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта, пользователь входит заново
