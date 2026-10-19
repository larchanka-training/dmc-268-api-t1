## ADDED Requirements

### Requirement: Время в ответах — UTC с Z

Каждое поле времени в JSON-ответах API и в данных событий потока статуса SHALL отдаваться в UTC в форме `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. В схеме OpenAPI такое поле SHALL быть `format: date-time` с `pattern` этой формы.

#### Scenario: Время прогона

- **WHEN** прогон записан со временем `2026-10-11T13:00:00+03:00`
- **THEN** в ответе и в событии `status` поле времени — `2026-10-11T10:00:00Z`

### Requirement: Offset списков в пределах bigint

Параметр `offset` списков SHALL быть целым от `0` до `9223372036854775807` (максимум bigint). Значение вне диапазона или слишком длинное число SHALL получать `422` с `code = VALIDATION_ERROR`, а не `500`, и без самого значения в теле. Верхней границы у `limit` нет: значение выше потолка приводится к потолку.

#### Scenario: Offset больше bigint

- **WHEN** вызывается `GET /api/v1/repositories?offset=9223372036854775808`
- **THEN** ответ — `422` с `VALIDATION_ERROR` и `{"where": "query.offset", "reason": "должно быть не больше 9223372036854775807"}`

### Requirement: Тело запроса проверяется строго

Тело изменяющего запроса SHALL приниматься только с полями из схемы в camelCase и без приведения типов: число строкой, строка числом, snake_case и лишнее поле получают `422` `VALIDATION_ERROR`. Строки и числа SHALL укладываться в границы колонок. Имя лишнего поля SHALL NOT попадать в ответ: `where` указывает на объект, `reason` — «лишнее поле».

#### Scenario: Число строкой

- **WHEN** `POST /api/v1/reviews` с сессией и своим `Origin` пришёл с `"pullRequestNumber": "5"`
- **THEN** ответ — `422` с `{"where": "body.pullRequestNumber", "reason": "ожидается целое число"}`

#### Scenario: Лишнее поле

- **WHEN** тело `POST /api/v1/reviews` содержит поле `<img src=x onerror=alert(1)>`
- **THEN** ответ — `422` с `{"where": "body", "reason": "лишнее поле"}`, имени поля в ответе нет
