## MODIFIED Requirements

### Requirement: Изменяющие запросы проверяют Origin

Запросы `POST`, `PUT`, `PATCH` и `DELETE` под `/api/v1/`, кроме приёма вебхуков, SHALL отклоняться с `403` и `code = ORIGIN_FORBIDDEN`, если заголовок `Origin` отсутствует или не совпадает с публичным адресом сервиса. Проверка `Origin` SHALL выполняться раньше разбора тела, проверки сессии и обращения к базе: такой запрос получает `403`, даже если тело — не JSON или сессии нет. Приём вебхуков под это правило SHALL NOT попадать.

#### Scenario: Запрос с чужого сайта

- **WHEN** `POST /api/v1/auth/logout` пришёл с cookie сессии и `Origin: https://evil.example`
- **THEN** ответ — `403` с `ORIGIN_FORBIDDEN`, сессия не удалена

#### Scenario: Чужой Origin без сессии

- **WHEN** `POST /api/v1/auth/logout` пришёл без cookie сессии и без `Origin` или с `Origin: https://evil.example`
- **THEN** ответ — `403` с `ORIGIN_FORBIDDEN`, а не `401` и не `204`
- **AND** ответ не ставит и не удаляет cookie `session`

#### Scenario: Битое тело без Origin

- **WHEN** `POST /api/v1/reviews` пришёл без `Origin` с `Content-Type: application/json` и телом `{bad`
- **THEN** ответ — `403` с `ORIGIN_FORBIDDEN`, а не `422`

#### Scenario: Вебхук

- **WHEN** `POST /api/v1/webhooks/github` пришёл без `Origin`
- **THEN** проверка `Origin` не применяется, действует проверка подписи
