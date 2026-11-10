## ADDED Requirements

### Requirement: Аналитика кабинета

`GET /api/v1/analytics` SHALL требовать сессию и SHALL отвечать `200` с агрегатами за период: `findings_by_day` — замечания по UTC-дню прогона, категории и важности, по дню, затем категории и важности в порядке перечислений; `runs` — число, средняя и p95 длительность прогонов; `by_repository` — то же и число замечаний по каждому репозиторию с учтёнными прогонами, по `full_name`. Учитываются прогоны `completed`, кроме `coverage_reason = balance`, и только новые замечания, без повторов тредов.

#### Scenario: Замечания по дням, категориям и важности

- **WHEN** в периоде прогоны видимого репозитория нашли два замечания `security`/`high` и одно `security`/`low` 3 октября по UTC
- **THEN** в `findings_by_day` есть `{date: 2026-10-03, category: security, severity: high, count: 2}` и `{date: 2026-10-03, category: security, severity: low, count: 1}`, пар без замечаний нет

#### Scenario: Длительность всего и по репозиторию

- **WHEN** в периоде завершены прогоны двух видимых репозиториев с длительностью
- **THEN** `runs` несёт их число, среднюю и p95 (`percentile_cont(0.95)`), а `by_repository` — те же величины, `repository_id`, `full_name` и `findings_count` по каждому репозиторию

#### Scenario: Прогон без денег и незавершённый прогон

- **WHEN** в периоде есть прогон с `coverage_reason = balance`, упавший и идущий прогоны
- **THEN** ни они, ни их замечания в агрегаты не входят

#### Scenario: Повтор треда бота

- **WHEN** более поздний прогон того же запроса на изменение повторил замечание открытого треда бота (`covered_by_finding_id` задан)
- **THEN** повтор не входит ни в `findings_by_day`, ни в `findings_count`, а новое замечание того же прогона входит

#### Scenario: Данных нет

- **WHEN** в периоде нет учтённых прогонов
- **THEN** ответ — `200` с пустыми `findings_by_day` и `by_repository` и `runs = {count: 0, average_duration_seconds: null, p95_duration_seconds: null}`

#### Scenario: Без сессии

- **WHEN** вызывается `GET /api/v1/analytics` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Контракт для фронтенда

- **WHEN** фронтенд читает `docs/openapi/openapi.json`
- **THEN** у `GET /api/v1/analytics` описаны `AnalyticsResponse` с обязательными `created_from`, `created_to`, `findings_by_day`, `runs`, `by_repository` и ответы `401`, `404`, `422`, `502`

### Requirement: Аналитика только по видимым репозиториям

`GET /api/v1/analytics` SHALL учитывать только репозитории, которые пользователь видит на GitHub, — правило `GET /reviews`; сбой GitHub SHALL давать `502 SCM_UNAVAILABLE`. `repository_id` SHALL сужать все агрегаты до этого репозитория; невидимый или несуществующий `repository_id` SHALL давать `404 REPOSITORY_NOT_FOUND`, одинаковый для обоих случаев.

#### Scenario: Чужой репозиторий

- **WHEN** у подключённого репозитория есть прогоны, а пользователь не видит его на GitHub
- **THEN** его прогоны и замечания не входят ни в `runs`, ни в `findings_by_day`, ни в `by_repository`

#### Scenario: Фильтр по репозиторию

- **WHEN** вызывается `GET /api/v1/analytics?repository_id=<видимый>`
- **THEN** все агрегаты — только по нему, в `by_repository` не больше одного элемента

#### Scenario: Невидимый или несуществующий репозиторий

- **WHEN** `repository_id` — репозиторий, которого пользователь не видит, или несуществующий id
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_FOUND`

#### Scenario: GitHub недоступен

- **WHEN** GitHub не ответил на запрос видимых репозиториев
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

### Requirement: Период аналитики

Период `GET /api/v1/analytics` SHALL идти по `created_at` прогона: `created_from` включительно, `created_to` исключительно, время с зоной. Без `created_to` SHALL браться текущее время, без `created_from` — за 30 дней до `created_to`. `created_from` позже `created_to` SHALL давать `422 VALIDATION_ERROR` в `query.created_to`, а без `created_to` — в `query.created_from`; период длиннее 180 дней — в `query.created_from`. Параметры SHALL проверяться раньше обращения к GitHub.

#### Scenario: Границы периода

- **WHEN** прогоны созданы ровно в `created_from`, ровно в `created_to` и между ними
- **THEN** учтены прогон в `created_from` и прогон между границами, прогон в `created_to` — нет

#### Scenario: Период по умолчанию

- **WHEN** вызывается `GET /api/v1/analytics` без `created_from` и `created_to`
- **THEN** `created_to` в ответе — текущее время, `created_from` — на 30 дней раньше

#### Scenario: Начало позже конца

- **WHEN** `created_from` позже `created_to`
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и `where = query.created_to`, к GitHub не обращались; без `created_to` и с `created_from` в будущем — `where = query.created_from`

#### Scenario: Слишком длинный период

- **WHEN** `created_to` позже `created_from` больше чем на 180 дней, в том числе когда `created_to` не задан
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и `where = query.created_from`, к GitHub не обращались

#### Scenario: День по UTC

- **WHEN** прогон создан в 20:30 UTC 3 октября, а зона сессии базы — `Asia/Tokyo`
- **THEN** его замечания относятся к дню `2026-10-03`
