## MODIFIED Requirements

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
