## RENAMED Requirements

- FROM: `### Requirement: Сервер отдаёт контракт API как есть`
- TO: `### Requirement: Схема API строится из кода`

## MODIFIED Requirements

### Requirement: Схема API строится из кода

`GET /openapi.json` SHALL отдавать схему OpenAPI, построенную из роутов и моделей приложения, а `/docs` — Swagger UI по ней. Каждый ответ ошибки в схеме SHALL быть `application/problem+json` с моделью своего кода, `422` — `ValidationProblem`. `docs/openapi/openapi.json` SHALL совпадать со схемой из кода: устаревший файл роняет CI.

#### Scenario: Схема API

- **WHEN** вызывается `GET /openapi.json`
- **THEN** ответ — `200`, и его JSON равен `docs/openapi/openapi.json`

#### Scenario: Просмотр документации

- **WHEN** браузер открывает `/docs`
- **THEN** ответ — `200` HTML Swagger UI, который читает `/openapi.json`

#### Scenario: Экспорт устарел

- **WHEN** ручка или модель ответа изменилась, а `docs/openapi/openapi.json` не обновлён
- **THEN** `uv run python scripts/export_openapi.py --check` завершается с кодом `1`

## ADDED Requirements

### Requirement: Запланированная ручка отвечает 501

Ручка, которая описана в схеме до реализации, SHALL быть в коде заглушкой с полной схемой запроса и ответов и SHALL отвечать `501` с `code = NOT_IMPLEMENTED`. Сессию, `Origin` и тело запроса она SHALL проверять так же, как реализованная.

#### Scenario: Ручной запуск ревью

- **WHEN** вызывается `POST /api/v1/reviews` с сессией, своим `Origin` и корректным телом
- **THEN** ответ — `501` `application/problem+json` с `code = NOT_IMPLEMENTED`

#### Scenario: Ручной запуск без сессии

- **WHEN** вызывается `POST /api/v1/reviews` со своим `Origin`, но без сессии
- **THEN** ответ — `401` с `code = UNAUTHENTICATED`
