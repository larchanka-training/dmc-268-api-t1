## ADDED Requirements

### Requirement: Разбор правил песочницы описан заглушкой

`POST /api/v1/rules/parse` SHALL быть описан в схеме с телом `RulesParseRequest` и ответом `RulesParseResponse` и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован.
Ручка SHALL требовать сессию и `Origin` сервиса и SHALL проверять тело раньше `501`.

#### Scenario: Разбор правил

- **WHEN** вызывается `POST /api/v1/rules/parse` с сессией, своим `Origin` и допустимым телом, в том числе с пустым `rules` и без `paths`
- **THEN** ответ - `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Без Origin

- **WHEN** вызывается `POST /api/v1/rules/parse` без `Origin` или с чужим `Origin`, с сессией или без
- **THEN** ответ - `403` с `code = ORIGIN_FORBIDDEN`, тело не разбирается

#### Scenario: Без сессии

- **WHEN** вызывается `POST /api/v1/rules/parse` со своим `Origin`, без сессии и с неверным телом
- **THEN** ответ - `401` с `code = UNAUTHENTICATED`

#### Scenario: Позиции правил в контракте

- **WHEN** фронтенд читает `RulesParseResponse` в `docs/openapi/openapi.json`
- **THEN** у принятого правила есть `line`, `checks` с `number`, `text` и `line` и `matched_paths` - файлы из `paths`, к которым правило применимо, у отклонённого раздела - `line` и `reason`

### Requirement: Тело разбора правил проверяется раньше 501

Тело `POST /api/v1/rules/parse` SHALL содержать `rules` - текст `.review/rules.md` не длиннее 32 768 символов, пустой допустим, - и необязательный `paths` - не больше 100 путей файлов длиной от 1 до 1024 символов.
Текст с NUL и лишние поля SHALL отклоняться.

#### Scenario: Неверное тело

- **WHEN** `rules` длиннее 32 768 символов или содержит NUL, в `paths` есть пустая строка, путей больше 100 или в теле есть лишнее поле
- **THEN** ответ - `422` с `code = VALIDATION_ERROR` и ошибкой в этом поле

### Requirement: Симуляция ревью описана заглушкой

`POST /api/v1/simulations` и `GET /api/v1/simulations/{simulation_id}` SHALL быть описаны в схеме с ответом `SimulationResponse` и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализованы.
Обе ручки SHALL требовать сессию, `POST` - ещё `Origin` сервиса; id симуляции SHALL проверяться раньше `501`.

#### Scenario: Запуск симуляции

- **WHEN** вызывается `POST /api/v1/simulations` с сессией, своим `Origin` и допустимым телом с `source.kind = code` или `source.kind = diff`
- **THEN** ответ - `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Чтение симуляции

- **WHEN** вызывается `GET /api/v1/simulations/{simulation_id}` с сессией и UUID в пути
- **THEN** ответ - `501` с `code = NOT_IMPLEMENTED`

#### Scenario: Чтение без сессии или с неверным id

- **WHEN** вызывается `GET /api/v1/simulations/{simulation_id}` без сессии или с сессией и не-UUID в пути
- **THEN** ответ - `401` с `code = UNAUTHENTICATED` или `422` с ошибкой в `path.simulation_id`

#### Scenario: Запуск без Origin

- **WHEN** вызывается `POST /api/v1/simulations` без `Origin` или с чужим `Origin`
- **THEN** ответ - `403` с `code = ORIGIN_FORBIDDEN`, тело не разбирается

#### Scenario: Ответы симуляции в контракте

- **WHEN** фронтенд читает операции симуляции в `docs/openapi/openapi.json`
- **THEN** у `POST` успех - `202` с `SimulationResponse` и есть `429` с `code = RATE_LIMITED` и заголовком `Retry-After`, у `GET` есть `404` с `code = SIMULATION_NOT_FOUND`

#### Scenario: Результат симуляции в контракте

- **WHEN** фронтенд читает `SimulationResponse` в `docs/openapi/openapi.json`
- **THEN** в нём есть статус из `SimulationStatus`, замечания с полями находки прогона и вердиктом верификатора, отброшенные замечания со стадией из `DiscardStage` и причиной, контекст модели по файлам и `expires_at` - момент, после которого симуляция удаляется

### Requirement: Вход симуляции проверяется раньше 501

Тело `POST /api/v1/simulations` SHALL быть `SimulationRequest`: `source` - объединение по `kind`, и `rules` - текст `.review/rules.md`, пустой допустим.
Вариант `kind = code` SHALL содержать `file_path` длиной от 1 до 1024 символов и непустой `content`, вариант `kind = diff` - непустой `diff`.
`content` и `diff` SHALL быть не длиннее 131 072 символов, `rules` - не длиннее 32 768; текст с NUL и лишние поля SHALL отклоняться.
Тело SHALL проверяться раньше `501`, но после `Origin` и сессии.

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

## MODIFIED Requirements

### Requirement: Типы и видимость событий общего потока описаны

Имя события `GET /api/v1/events` SHALL быть `<ресурс>.<действие>` в поле `event:`, данные — модель своего типа, перечисленная в `x-sse-events`. Клиент SHALL игнорировать незнакомые типы: новые добавляются без правки клиентов. Пользователь SHALL получать только видимые ему события; видимость задаёт правило типа события. Фильтров у потока нет.

#### Scenario: Сегодняшние типы

- **WHEN** фронтенд читает ответ потока и `components/schemas` в `docs/openapi/openapi.json`
- **THEN** в `x-sse-events` есть `run.created`, `run.status_changed` и `run.finished` со ссылкой на `ReviewRunListItem`, `simulation.status_changed` и `simulation.finished` со ссылкой на `SimulationSummary` и `ping` с пустым объектом, а `StreamEventType` перечисляет эти шесть имён

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

#### Scenario: Типы симуляции

- **WHEN** фронтенд читает описание потока
- **THEN** в нём сказано, что события `simulation.*` видит только автор симуляции, при переходе симуляции в итоговый статус (`completed` или `failed`) приходит только `simulation.finished` и что пока симуляция не реализована, эти события не отправляются
