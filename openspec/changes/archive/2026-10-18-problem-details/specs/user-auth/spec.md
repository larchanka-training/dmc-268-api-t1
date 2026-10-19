## MODIFIED Requirements

### Requirement: Ответ без сессии удаляет cookie

Ручка, требующая сессию, SHALL отвечать `401` с `code = UNAUTHENTICATED` в теле Problem Details, если действительной сессии нет. Если запрос пришёл с cookie `session`, которая недействительна, ответ SHALL её удалять.

#### Scenario: Недействительная cookie

- **WHEN** запрос пришёл с cookie `session`, которой нет в базе
- **THEN** ответ — `401` с `UNAUTHENTICATED` и `Set-Cookie`, удаляющим `session`

### Requirement: Изменяющие запросы проверяют Origin

Запросы `POST`, `PUT`, `PATCH` и `DELETE` к ручкам, требующим сессию, и к выходу SHALL отклоняться с `403` и `code = ORIGIN_FORBIDDEN`, если заголовок `Origin` отсутствует или не совпадает с публичным адресом сервиса. Проверка `Origin` SHALL выполняться раньше проверки сессии и без обращения к базе: такой запрос получает `403`, даже если действительной сессии нет. Приём вебхуков под это правило SHALL NOT попадать.

#### Scenario: Запрос с чужого сайта

- **WHEN** `POST /api/v1/auth/logout` пришёл с cookie сессии и `Origin: https://evil.example`
- **THEN** ответ — `403` с `ORIGIN_FORBIDDEN`, сессия не удалена

#### Scenario: Чужой Origin без сессии

- **WHEN** `POST /api/v1/auth/logout` пришёл без cookie сессии и без `Origin` или с `Origin: https://evil.example`
- **THEN** ответ — `403` с `ORIGIN_FORBIDDEN`, а не `401` и не `204`
- **AND** ответ не ставит и не удаляет cookie `session`

#### Scenario: Вебхук

- **WHEN** `POST /api/v1/webhooks/github` пришёл без `Origin`
- **THEN** проверка `Origin` не применяется, действует проверка подписи
