## REMOVED Requirements

### Requirement: Дифф и шаги прогона описаны заглушками

**Reason**: Шаги прогона реализованы, а сценарий «Шаги прогона» требует от `/steps` ответа `501`; MODIFIED-блок не может убрать сценарий.

**Migration**: Заглушку диффа описывает «Дифф прогона описан заглушкой», шаги — «Шаги прогона отдаются по порядку начала».

## ADDED Requirements

### Requirement: Дифф прогона описан заглушкой

`GET /api/v1/reviews/{review_run_id}/diff` SHALL быть описан в схеме с полной моделью ответа и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован. Сессию и формат id прогона он SHALL проверять так же, как реализованные ручки.

#### Scenario: Дифф прогона

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/diff` с сессией
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
