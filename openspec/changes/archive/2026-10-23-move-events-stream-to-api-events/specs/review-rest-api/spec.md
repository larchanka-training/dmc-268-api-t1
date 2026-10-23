## MODIFIED Requirements

### Requirement: Список прогонов описан заглушкой

`GET /api/v1/reviews` SHALL быть описан в схеме с полной моделью ответа и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован; сессию SHALL требовать. Ответ SHALL содержать `items` — строки `ReviewRunListItem` с `MergeRequestSummary`, числом находок и обязательным `last_progress_at` — и `total` — число прогонов под фильтрами без учёта `limit` и `offset`.

#### Scenario: Список прогонов

- **WHEN** вызывается `GET /api/v1/reviews` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Список без сессии

- **WHEN** вызывается `GET /api/v1/reviews` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Контракт списка для фронтенда

- **WHEN** фронтенд читает `docs/openapi/openapi.json`
- **THEN** в `ReviewRunListResponse` есть обязательные `items` и `total`

#### Scenario: Время последнего перехода в строке списка

- **WHEN** фронтенд читает `ReviewRunListItem` в `docs/openapi/openapi.json`
- **THEN** `last_progress_at` — обязательное поле, время в UTC, как у `ReviewRunResponse`

## REMOVED Requirements

### Requirement: Поток изменений прогонов описан заглушкой

**Reason**: Поток стал общим для кабинета и переехал на `GET /api/v1/events`; у заглушки `/reviews/events` клиентов не было.

**Migration**: Заглушку описывают «Общий поток событий описан заглушкой» и «Типы и видимость событий общего потока описаны». `GET /api/v1/reviews/events` теперь разбирается как прогон с неверным id.

### Requirement: Порядок версий строки в потоке прогонов описан

**Reason**: Правило переехало вместе с потоком на `GET /api/v1/events`.

**Migration**: То же правило задаёт «Порядок версий строки в общем потоке событий описан».

## ADDED Requirements

### Requirement: Общий поток событий описан заглушкой

`GET /api/v1/events` SHALL быть описан в схеме без параметров, с ответом `200` типа `text/event-stream`, и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован; сессию SHALL требовать. Поток один на весь кабинет; описание SHALL называть его заменой потока одного прогона `GET /api/v1/reviews/{review_run_id}/events`.

#### Scenario: Поток событий

- **WHEN** вызывается `GET /api/v1/events` с сессией
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Поток без сессии

- **WHEN** вызывается `GET /api/v1/events` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Путь без параметров

- **WHEN** фронтенд читает `GET /api/v1/events` в `docs/openapi/openapi.json`
- **THEN** у операции нет параметров, ответ `200` — `text/event-stream`

#### Scenario: Старого пути нет

- **WHEN** фронтенд читает `docs/openapi/openapi.json`
- **THEN** пути `/api/v1/reviews/events` в нём нет, а `GET /api/v1/reviews/events` отвечает `422` проверки id прогона

### Requirement: Типы и видимость событий общего потока описаны

Имя события `GET /api/v1/events` SHALL быть `<ресурс>.<действие>` в поле `event:`, данные — модель своего типа, перечисленная в `x-sse-events`. Клиент SHALL игнорировать незнакомые типы: новые добавляются без правки клиентов. Пользователь SHALL получать только видимые ему события; видимость задаёт правило типа события. Фильтров у потока нет.

#### Scenario: Сегодняшние типы

- **WHEN** фронтенд читает ответ потока и `components/schemas` в `docs/openapi/openapi.json`
- **THEN** в `x-sse-events` есть `run.created`, `run.status_changed` и `run.finished` со ссылкой на `ReviewRunListItem` и `ping` с пустым объектом, а `StreamEventType` перечисляет эти четыре имени

#### Scenario: Итоговый статус

- **WHEN** фронтенд читает описание потока
- **THEN** в нём сказано, что при переходе прогона в итоговый статус приходит только `run.finished`

#### Scenario: Видимость прогонов

- **WHEN** фронтенд читает описание потока
- **THEN** события `run.*` уходят только по прогонам репозиториев, к которым у пользователя есть доступ у провайдера; набор считается при подключении токеном пользователя, отобранный доступ учитывается при переподключении

#### Scenario: Незнакомый тип

- **WHEN** фронтенд читает описание потока
- **THEN** в нём сказано, что клиент игнорирует незнакомые типы событий

### Requirement: Порядок версий строки в общем потоке событий описан

Описание `GET /api/v1/events` SHALL задавать правило для клиента: upsert строки прогона по `id`; побеждает больший `updated_at`, при равном — версия, пришедшая позже. События SHALL уходить в момент коммита записи прогона (NOTIFY из транзакции записи). Истории нет: при подключении и после переподключения клиент перезапрашивает снимок REST.

#### Scenario: Правило в контракте

- **WHEN** фронтенд читает описание `GET /api/v1/events` в `docs/openapi/openapi.json`
- **THEN** в нём есть правило выбора версии по `updated_at`, источник событий NOTIFY и перезапрос снимка `GET /reviews/{review_run_id}` или `GET /reviews` после события `open`
