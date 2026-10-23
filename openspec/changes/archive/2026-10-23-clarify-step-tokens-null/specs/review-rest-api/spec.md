## MODIFIED Requirements

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
