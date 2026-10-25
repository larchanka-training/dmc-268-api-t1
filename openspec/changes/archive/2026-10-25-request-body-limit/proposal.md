## Why

Приём вебхука (`app/api/webhooks.py`) делает `await request.body()` до проверки подписи, ручка открыта без сессии. Ни uvicorn, ни инфраструктура лимита тела не ставят: любой может прислать многогигабайтное тело — с `Content-Length` или потоком chunked без него — и занять память процесса до того, как сервис узнает, подписано ли оно. То же у ручек с JSON-телом (`POST /reviews`, `POST /repositories`, `PUT …/settings`): тело читается до проверки сессии. Задача #72.

Встроенный `max_body_size` Starlette 1.9 закрывает поток, но отказ по `Content-Length` отдаёт `413 text/plain` «Content Too Large» и подменяет им уже собранный ответ — мимо Problem Details и без `X-Trace-ID` (проверено, правило в `.agents/rules/backend.md`).

## What Changes

- Своё ASGI-middleware `RequestBodyLimitMiddleware` (`app/api/body_limit.py`) ограничивает тело запроса API: у приёма вебхука — `WEBHOOK_BODY_MAX_BYTES` (по умолчанию 25 МиБ — не меньше потолка доставки GitHub), у остальных ручек — `REQUEST_BODY_MAX_BYTES` (1 МиБ) до роутинга, разбора тела, сессии и подписи вебхука: `Content-Length` больше лимита — отказ без чтения тела; иначе тело читается с подсчётом байтов и обрывается на куске, где сумма перешла лимит, — так ограничены и chunked без длины, и поток длиннее объявленного.
- Отказ — `413` с новым кодом `REQUEST_BODY_TOO_LARGE` (`RequestBodyTooLargeProblem`) в формате RFC 9457 с `trace_id` и заголовком `X-Trace-ID`, с `Connection: close`: недочитанное тело не остаётся в соединении.
- Контракт: `413` объявлен у ручек с телом и у приёма вебхука; `docs/openapi/openapi.json` перегенерирован.

## Capabilities

### New Capabilities

Нет.

### Modified Capabilities

- `webhook-intake`: ADDED «Тело вебхука читается с лимитом до подписи».
- `review-rest-api`: ADDED «Тело запроса ограничено по размеру» (новый код ошибки `REQUEST_BODY_TOO_LARGE`).

## Impact

- Код: `app/api/body_limit.py` (новый), `app/api/factory.py`, `app/api/problems.py`, `app/api/errors.py`, `app/config.py` (`request_body_max_bytes`, `webhook_body_max_bytes`), `responses` в `app/api/webhooks.py`, `app/api/reviews.py`, `app/api/repositories.py`.
- Контракт API: новый код ошибки и ответ `413` у четырёх операций; фронтенду правка не обязательна — тело его запросов на порядки меньше 1 МиБ.
- Конфигурация: `REQUEST_BODY_MAX_BYTES`, `WEBHOOK_BODY_MAX_BYTES` в `.env.example`. Документация: `docs/BACKEND_ARCHITECTURE.md` («Сессия на ручках», «Модель угроз»).
