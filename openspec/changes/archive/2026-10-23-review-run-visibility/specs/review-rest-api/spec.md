## ADDED Requirements

### Requirement: Прогон виден только тому, кто видит его репозиторий

`GET /api/v1/reviews/{review_run_id}`, `/steps` и `/diff` SHALL отдавать прогон, только если его репозиторий виден пользователю на GitHub, — то же правило, что у `GET /reviews` и `GET /events`. Прогон невидимого репозитория SHALL получать тот же ответ `404` с `code = REVIEW_NOT_FOUND` и тем же `detail`, что несуществующий. Прогон репозитория не с GitHub SHALL не быть виден.

#### Scenario: Прогон чужого репозитория

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` с сессией, а репозиторий прогона GitHub по токену пользователя не отдаёт
- **THEN** ответ — `404` с `code = REVIEW_NOT_FOUND` и тем же `detail`, что у несуществующего прогона

#### Scenario: Шаги и дифф чужого прогона

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/steps` или `/diff` для прогона невидимого репозитория
- **THEN** ответ — `404` с `code = REVIEW_NOT_FOUND`, для `/diff` — не `501`

### Requirement: Ответы GitHub не выдают, есть ли прогон

Ручки одного прогона SHALL обращаться к GitHub за видимостью при любом id — до чтения прогона, и для несуществующего тоже: иначе сбой GitHub, отозванный токен или время ответа выдавали бы, что id есть. Сбой GitHub SHALL давать `502` с `code = SCM_UNAVAILABLE`, отозванный токен — `401` с `code = UNAUTHENTICATED` и закрытую сессию, одинаково для существующего и несуществующего id.

#### Scenario: Несуществующий прогон

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}` с id, которого нет
- **THEN** ответ — `404` с `code = REVIEW_NOT_FOUND`, запрос к GitHub был

#### Scenario: GitHub недоступен при проверке

- **WHEN** GitHub не ответил на запрос списка репозиториев — прогон с этим id есть или его нет
- **THEN** ответ — `502` с `code = SCM_UNAVAILABLE`

#### Scenario: Токен отозван на GitHub

- **WHEN** GitHub отклонил токен пользователя при проверке видимости
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`, сессия закрыта

## MODIFIED Requirements

### Requirement: Дифф прогона описан заглушкой

`GET /api/v1/reviews/{review_run_id}/diff` SHALL быть описан в схеме с полной моделью ответа и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`, пока не реализован. Сессию, формат id и видимость прогона он SHALL проверять так же, как реализованные ручки, — до ответа `501`.

#### Scenario: Дифф прогона

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/diff` с сессией для прогона, видимого пользователю
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Дифф без сессии

- **WHEN** вызывается `GET /api/v1/reviews/{review_run_id}/diff` без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`
