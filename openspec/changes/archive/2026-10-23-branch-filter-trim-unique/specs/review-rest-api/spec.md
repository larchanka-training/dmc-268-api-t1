## MODIFIED Requirements

### Requirement: Тело записи настроек проверяется раньше 501

`PUT /api/v1/repositories/{repository_id}/settings` SHALL принимать `RepositorySettingsUpdate` — полный набор настроек: все три поля обязательны, лишние поля запрещены. Паттерн в `branch_filter` SHALL быть непустым после обрезки пробелов и не длиннее 200 символов, без повторов, паттернов SHALL быть не больше 50. Id репозитория и тело SHALL проверяться раньше ответа `501`.

#### Scenario: Неверный id

- **WHEN** вызывается `PUT` настроек с сессией, своим `Origin` и не-UUID в пути
- **THEN** ответ — `422` с `code = VALIDATION_ERROR` и ошибкой в `path.repository_id`

#### Scenario: Неизвестная важность

- **WHEN** в теле `min_severity` вне `FindingSeverity`
- **THEN** ответ — `422` с ошибкой в `body.min_severity`

#### Scenario: Лишнее или пропущенное поле

- **WHEN** в теле есть поле вне модели или нет одного из трёх полей
- **THEN** ответ — `422` с `code = VALIDATION_ERROR`

#### Scenario: Пустой или длинный паттерн

- **WHEN** в `branch_filter` есть пустая строка или строка длиннее 200 символов
- **THEN** ответ — `422` с ошибкой в `body.branch_filter.<индекс>`

#### Scenario: Слишком много паттернов

- **WHEN** в `branch_filter` 51 паттерн
- **THEN** ответ — `422` с ошибкой в `body.branch_filter`

#### Scenario: Без сессии тело не проверяется

- **WHEN** вызывается `PUT` настроек со своим `Origin`, без сессии и с неверным телом
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`

#### Scenario: Паттерн из пробелов

- **WHEN** в `branch_filter` есть паттерн `"   "`
- **THEN** ответ — `422` с причиной «длина строки должна быть не меньше 1» в `body.branch_filter.<индекс>`

#### Scenario: Повтор паттерна

- **WHEN** в `branch_filter` есть `main` и ` main ` (совпадают после обрезки пробелов)
- **THEN** ответ — `422` с причиной «паттерны веток повторяются» в `body.branch_filter`
