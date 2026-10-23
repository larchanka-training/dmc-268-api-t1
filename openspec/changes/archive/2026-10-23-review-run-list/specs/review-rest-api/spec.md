## ADDED Requirements

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

`GET /api/v1/reviews` SHALL отдавать только прогоны репозиториев, к которым у пользователя есть доступ на GitHub, — то же правило, что у `GET /repositories/available` и потока `GET /events`. Набор видимых SHALL считаться токеном пользователя на каждый запрос; сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`.

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

## REMOVED Requirements

### Requirement: Список прогонов описан заглушкой

**Reason**: список реализован — требование «Список прогонов ревью».
**Migration**: фронтенду — `pnpm api:sync`; вместо `501` у ручки в контракте `502 SCM_UNAVAILABLE`.

### Requirement: Параметры списка прогонов проверяются раньше 501

**Reason**: ответа `501` больше нет — требование «Параметры списка прогонов проверяются раньше обращения к GitHub».
**Migration**: не нужна, проверки параметров прежние.
