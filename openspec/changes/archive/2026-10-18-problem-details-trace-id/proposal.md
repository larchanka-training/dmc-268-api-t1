## Why

Ревью PR #70 нашло в формате ошибок несколько мест, которые расходятся со стандартами или
друг с другом:

- идентификатор запроса — свой uuid4 в `X-Request-ID`/`request_id`; когда подключим
  OpenTelemetry, логи и трассы будут связаны trace id W3C, и второй идентификатор пришлось
  бы сопоставлять с ним руками;
- на `500` сервер ничего не писал в лог сам — найти по идентификатору из сообщения
  пользователя было нечего, кроме стектрейса uvicorn без идентификатора;
- `instance` брался из раскодированного `request.url.path`: `/api/v1/a b` — не
  URI-reference, а тесты обходили «эхо ввода» в `instance` вырезанием строки;
- `/openapi.json` и `/docs` отдавали схему, которую FastAPI строит из роутов, — с ошибками
  `{"detail": …}`, которых сервер не отдаёт;
- `HTTPException` со статусом, кроме `404`/`405`, превращался в `500`, теряя статус и
  заголовки (`Retry-After`).

## What Changes

- **BREAKING** `request_id` → `trace_id`, заголовок `X-Request-ID` → `X-Trace-ID`. Значение —
  32 строчных hex (формат trace-id W3C Trace Context, не все нули), выдаётся сервером на
  каждый запрос; входящие `X-Trace-ID`, `X-Request-ID`, `traceparent` не принимаются. При
  подключении OpenTelemetry источником станет trace id OTel, формат не изменится.
- На необработанное исключение сервер пишет одну строку лога уровня error: `trace_id`,
  метод, путь без query. Стектрейс по-прежнему пишет uvicorn.
- `instance` — путь из `raw_path`, закодированный, без query. Запрет повторять переданное
  значение относится к `detail` и `errors`.
- Новый код `HTTP_ERROR` («Ошибка запроса»): `HTTPException` фреймворка или зависимости со
  статусом 4xx/5xx, у которого нет своего кода, — со статусом и заголовками исключения.
  Текст исключения в тело не идёт.
- `/openapi.json` и Swagger UI на `/docs` отдают `docs/openapi/openapi.yaml` как есть,
  ReDoc выключен. Схема из роутов строится только в тестах для сверки с контрактом.
  `pyyaml` переходит в зависимости приложения, файл контракта копируется в образ.

## Impact

- Код: `app/api/trace_id.py` (вместо `request_id.py`), `app/api/errors.py`,
  `app/api/openapi_contract.py`, `app/api/factory.py`; `Dockerfile`, `pyproject.toml`,
  `uv.lock`.
- Контракт и документы: `docs/openapi/openapi.yaml` (`Problem.trace_id`,
  `components.headers.X-Trace-ID` с `pattern`, `ProblemHttpError`, таблица `ErrorCode`),
  `docs/pipeline/PIPELINE_SPEC.md` §9, `README.md`, `AGENTS.md`, `.agents/rules/backend.md`,
  `.agents/templates/backend/endpoint.md`, `openspec/config.yaml`.
- Фронт читает `trace_id` вместо `request_id`; ветвление по `code` не меняется.
- Тесты: `tests/test_problem_details.py`, `tests/test_docs.py` (сверка через `get_openapi`,
  `pattern` в мини-валидаторе), `tests/test_review_events_api.py`, `tests/test_reviews_api.py`.
