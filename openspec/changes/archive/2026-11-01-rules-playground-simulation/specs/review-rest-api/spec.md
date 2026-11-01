## REMOVED Requirements

### Requirement: Симуляция ревью описана заглушкой

**Reason**: ручки реализованы и больше не отвечают `501`.

**Migration**: поведение описывают требования «Запуск симуляции ревью» и «Чтение симуляции ревью»; схемы запроса и ответа те же, итог - опросом `GET`.

### Requirement: Вход симуляции проверяется раньше 501

**Reason**: ручка реализована: тело проверяется раньше записи симуляции, а не раньше `501`.

**Migration**: те же ограничения, лимит размера правил ревью и проверка, что во входе есть что ревьюить, - в требовании «Вход симуляции проверяется».

## ADDED Requirements

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
