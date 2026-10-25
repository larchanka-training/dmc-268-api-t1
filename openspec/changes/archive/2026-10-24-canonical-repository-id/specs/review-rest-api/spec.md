## ADDED Requirements

### Requirement: Подключение хранит канонический id репозитория

`provider_id` в теле `POST /api/v1/repositories` SHALL соответствовать `^[1-9][0-9]*$` (число из ASCII-цифр без ведущих нулей), иначе ответ — `422 VALIDATION_ERROR` без запроса к GitHub. Записанный `provider_id` и проверка «уже подключён» SHALL брать id из ответа GitHub, а не из запроса.

#### Scenario: Id с ведущим нулём

- **WHEN** вызывается `POST /api/v1/repositories` с сессией, своим `Origin` и `"provider_id": "0101"`
- **THEN** ответ — `422` с `{"where": "body.provider_id", "reason": "недопустимое значение"}`, к GitHub запросов нет

#### Scenario: Не-ASCII цифры

- **WHEN** `provider_id` — `"١٠١"` или `"²"`
- **THEN** ответ — `422`, к GitHub запросов нет

#### Scenario: Записан id из ответа GitHub

- **WHEN** GitHub на `GET /repositories/{id}` отдаёт репозиторий с `id`, запись которого отличается от присланной
- **THEN** в `repositories.provider_id` записан `id` из ответа GitHub, а если репозиторий с этим id уже зарегистрирован — ответ `409 REPOSITORY_ALREADY_CONNECTED`
