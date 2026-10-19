## 1. Реализация

- [x] 1.1 `app/api/request_id.py`: ASGI-middleware `RequestIdMiddleware` — uuid4 в `request.state` и заголовок `X-Request-ID`
- [x] 1.2 `app/api/errors.py`: построитель `problem_response`, `ErrorCode`, `TITLES`, исключение `ApiError` и обработчики `422`, `404`/`405` маршрутизатора и `500`
- [x] 1.3 `401` и `403` сессии — подклассы `ApiError`, удаление cookie — в `Unauthenticated.finish`; вебхуки бросают `ApiError` вместо `_api_error`

## 2. Контракт и документы

- [x] 2.1 `openapi.yaml`: `Problem`, `ValidationProblem`, схемы кодов, каталог `AnyProblem`, `ErrorCode` с таблицей; общие ответы и `X-Request-ID`; `ApiError` удалена
- [x] 2.2 `PIPELINE_SPEC.md` §9 — формат по RFC 9457; шаблон эндпоинта — как бросать ошибку

## 3. Тесты и проверки

- [x] 3.1 `tests/test_problem_details.py`: построитель и обработчики, `Content-Type`, `X-Request-ID`
- [x] 3.2 `tests/test_docs.py`: каталог кодов, коды сервера, ответы ошибок и примеры против схем
- [x] 3.3 `ruff`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict`, `redocly lint` проходят
