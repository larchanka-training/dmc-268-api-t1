## Why

Ошибки `/api/v1` отдавались своим телом `ApiError {code, message}`, а ошибки вне ручек —
неизвестный путь, неразрешённый метод, необработанное исключение — телами Starlette
(`{"detail": "Not Found"}`, текст `Internal Server Error`). Решено перейти на стандартный
формат RFC 9457 Problem Details: клиенту и прокси он знаком, а `code` остаётся тем же, и
кабинет по-прежнему ветвится по нему. Связать ответ с логами сервера было нечем — нужен
идентификатор запроса.

## What Changes

- **BREAKING** Тело любой ошибки приложения — `application/problem+json`: `type`
  (`urn:dmc-268:problem:<код>`), `title`, `status`, `detail` (бывший `message`),
  `instance`, расширения `code` (прежний закрытый список) и `request_id`. У
  `VALIDATION_ERROR` — `errors: [{where, reason}]` вместо склеенной строки.
- Новые коды: `ROUTE_NOT_FOUND` (`404` неизвестного пути), `METHOD_NOT_ALLOWED` (`405`,
  заголовок `Allow` сохраняется), `INTERNAL_ERROR` (`500` без деталей исключения).
- Каждый ответ несёт `X-Request-ID` — uuid4, выданный сервером; входящий заголовок не
  принимается. Значение лежит в `request.state` для будущих логов; логирование не
  меняется.
- `openapi.yaml`: схемы `Problem`, `ValidationProblem`, каталог `AnyProblem` с
  `discriminator` по `code`, `ErrorCode` с таблицей; общие ответы в `components.responses`
  с примерами; заголовок `X-Request-ID` в `components.headers`. Схема `ApiError` удалена.

## Impact

- Код: `app/api/errors.py` (построитель `problem_response`, `ErrorCode`, обработчики),
  `app/api/request_id.py` (middleware), `app/api/session.py` (`401` и `403` — подклассы
  `ApiError`), `app/api/webhooks.py` (без своего `_api_error`), `app/api/factory.py`.
- Контракт и документы: `docs/openapi/openapi.yaml`, `docs/pipeline/PIPELINE_SPEC.md` §9,
  `.agents/templates/backend/endpoint.md`.
- Фронт читает `detail` вместо `message` и `errors` у `422`; ветвление по `code` не меняется.
- Тесты: `tests/test_problem_details.py`, тела ошибок в тестах ручек, `tests/test_docs.py`
  сверяет каталог ошибок, коды сервера и примеры со схемами.
