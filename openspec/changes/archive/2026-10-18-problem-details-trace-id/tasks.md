## 1. Реализация

- [x] 1.1 `app/api/trace_id.py`: `TraceIdMiddleware`, `new_trace_id`, `trace_id_of`, заголовок `X-Trace-ID`
- [x] 1.2 `app/api/errors.py`: `trace_id` в теле, `instance` из `raw_path`, строка лога на `500`, `HTTP_ERROR` для прочих `HTTPException`
- [x] 1.3 `app/api/openapi_contract.py` и `create_app`: `/openapi.json` и `/docs` по `openapi.yaml`, ReDoc выключен; `pyyaml` в зависимостях, контракт в образе

## 2. Контракт и документы

- [x] 2.1 `openapi.yaml`: `trace_id`, `X-Trace-ID` с `pattern`, `instance` как URI-reference, `ProblemHttpError` в каталоге, таблица `ErrorCode`
- [x] 2.2 `PIPELINE_SPEC.md` §9, `README.md`, `AGENTS.md`, `.agents/rules/backend.md`, шаблон эндпоинта, `openspec/config.yaml`

## 3. Тесты и проверки

- [x] 3.1 `tests/test_problem_details.py`: `trace_id`, входящие заголовки, `instance`, `HTTP_ERROR`, строка лога, контракт по `/openapi.json`, тела всех кодов против каталога
- [x] 3.2 `tests/test_docs.py`: сверка с роутами через `get_openapi`, `pattern` в мини-валидаторе; `X-Trace-ID` у потока SSE
- [x] 3.3 `ruff`, `lint-imports`, `mypy`, `pytest`, `openspec validate --all --strict`, `redocly lint` проходят
