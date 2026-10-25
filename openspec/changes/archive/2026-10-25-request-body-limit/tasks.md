## 1. Воспроизведение

- [x] 1.1 Красные тесты на коде до правки (`tests/test_request_body_limit.py`, ASGI напрямую): `Content-Length` больше лимита — тело читается и проверяется подпись; chunked больше лимита с верной подписью — тело читается целиком, подпись проверяется; поток длиннее объявленной длины; `POST /reviews` без сессии — тело больше лимита читается целиком, `422`; `413` в формате Problem Details с `X-Trace-ID`. Тело ровно на лимите и неверная подпись в пределах лимита — как раньше

## 2. Правка

- [x] 2.1 Код `REQUEST_BODY_TOO_LARGE`, модель `RequestBodyTooLargeProblem` в `AnyProblem` (`app/api/problems.py`), `ERRORS_413_BODY` (`app/api/errors.py`)
- [x] 2.2 Настройка `request_body_max_bytes`, 25 МиБ (`app/config.py`, тест `tests/test_config.py`, `.env.example`)
- [x] 2.3 Адаптер HTTP `RequestBodyLimitMiddleware` (`app/api/body_limit.py`), подключён в `app/api/factory.py` внутри `TraceIdMiddleware` и `OriginMiddleware`
- [x] 2.4 `413` в `responses` у приёма вебхука, `POST /reviews`, `POST /repositories`, `PUT /repositories/{id}/settings`; тест контракта — у каждой операции с телом
- [x] 2.5 `uv run python scripts/export_openapi.py`

## 3. Проверка и документация

- [x] 3.1 Живой uvicorn и curl: `Content-Length` 30 МиБ, chunked 30 МиБ, подписанная доставка
- [x] 3.2 `docs/BACKEND_ARCHITECTURE.md`: «Сессия на ручках», «Модель угроз»
