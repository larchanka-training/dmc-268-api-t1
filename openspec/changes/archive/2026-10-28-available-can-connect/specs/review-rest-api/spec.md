## ADDED Requirements

### Requirement: Признак права на подключение в списке доступных

Каждая строка `GET /api/v1/repositories/available` SHALL нести `can_connect`: `true`, если у пользователя на репозитории роль admin или maintain, иначе `false`. Правило SHALL совпадать с проверкой прав `POST /api/v1/repositories`, права SHALL браться из того же ответа GitHub, что и список. `POST /repositories` SHALL проверять права сам, независимо от поля.

#### Scenario: Роль admin или maintain

- **WHEN** GitHub отдаёт `octo-org/api` с `permissions.admin = true` или `permissions.maintain = true`
- **THEN** у `octo-org/api` в ответе `can_connect = true`, и `POST /repositories` с его `provider_id` его подключает (`201`) или отвечает `409`, если он уже подключён

#### Scenario: Роль ниже maintain

- **WHEN** GitHub отдаёт `octo-org/web` с ролью read, triage или write
- **THEN** у `octo-org/web` в ответе `can_connect = false`, и `POST /repositories` с его `provider_id` его не подключает: `403` с `code = REPOSITORY_MANAGE_REQUIRED`, а публичный только с правом чтения — `404`, как невидимый
