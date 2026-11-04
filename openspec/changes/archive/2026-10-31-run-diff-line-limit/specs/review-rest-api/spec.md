## MODIFIED Requirements

### Requirement: Отказы диффа прогона

Ручка диффа SHALL отвечать problem+json с кодом из каталога, а не пустым диффом и не `500`: нет `base_sha` — `409` `DIFF_UNAVAILABLE`; приложение не установлено или установка приостановлена — `404` `REPOSITORY_NOT_IN_INSTALLATION`; GitHub не знает коммит — `410` `COMMIT_NOT_FOUND`; дифф больше лимита — `422` `DIFF_TOO_LARGE`; прочий сбой GitHub — `502` `SCM_UNAVAILABLE`. Лимит — `RUN_DIFF_MAX_BYTES` (2 МиБ), 50 000 строк (`RUN_DIFF_MAX_LINES`) или отказ GitHub (compare отвечает `422`).

#### Scenario: Прогон без base_sha

- **WHEN** вызывается `/diff` для видимого прогона, у которого `base_sha` не записан
- **THEN** ответ — `409` с `code = DIFF_UNAVAILABLE`, запросов установки и диффа к GitHub не было

#### Scenario: Приложение снято с репозитория

- **WHEN** GitHub отвечает, что приложение на репозиторий прогона не установлено или установка приостановлена
- **THEN** ответ — `404` с `code = REPOSITORY_NOT_IN_INSTALLATION`, запроса диффа не было

#### Scenario: Коммит прогона исчез у провайдера

- **WHEN** GitHub отвечает `404` на сравнение `base_sha...head_sha` прогона
- **THEN** ответ — `410` с `code = COMMIT_NOT_FOUND`

#### Scenario: Дифф больше лимита

- **WHEN** дифф пары коммитов прогона больше `RUN_DIFF_MAX_BYTES`
- **THEN** ответ — `422` с `code = DIFF_TOO_LARGE`, тело ответа GitHub не дочитано

#### Scenario: Дифф больше 50 000 строк

- **WHEN** дифф пары коммитов прогона не больше `RUN_DIFF_MAX_BYTES`, но в нём больше 50 000 строк
- **THEN** ответ — `422` с `code = DIFF_TOO_LARGE`, дифф не разбирается и в кэш не записывается: повторный запрос снова идёт к GitHub

#### Scenario: Дифф ровно в 50 000 строк

- **WHEN** в диффе пары коммитов прогона ровно 50 000 строк и он не больше `RUN_DIFF_MAX_BYTES`
- **THEN** ответ — `200` с диффом, сырой дифф записан в кэш

#### Scenario: GitHub отказался строить дифф

- **WHEN** GitHub отвечает `422` на сравнение `base_sha...head_sha` прогона (дифф слишком велик для GitHub)
- **THEN** ответ — `422` с `code = DIFF_TOO_LARGE`, а не `502` `SCM_UNAVAILABLE`

#### Scenario: GitHub недоступен при загрузке диффа

- **WHEN** GitHub не ответил на запрос установки или диффа, ответил `5xx` или отказом лимита
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`
