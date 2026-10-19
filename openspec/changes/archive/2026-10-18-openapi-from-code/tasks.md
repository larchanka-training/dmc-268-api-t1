## 1. Код

- [x] 1.1 `app/api/problems.py`: `ErrorCode` с `NOT_IMPLEMENTED`, модели `Problem`, кодов, `ValidationProblem`, объединение `AnyProblem`
- [x] 1.2 `app/api/errors.py`: `problem_response` строит тело через модель кода; словари `ERRORS_*` для `responses`
- [x] 1.3 `app/api/openapi.py`: `application/problem+json` у ошибок, `X-Trace-ID` у ответов, компонент события SSE; `create_app` — метаданные, `default` у всех ручек
- [x] 1.4 Роуты: модели ответов с enum'ами домена, `responses` с ошибками, схема безопасности `session`; заглушка `POST /api/v1/reviews` — `501 NOT_IMPLEMENTED`

## 2. Экспорт и сборка

- [x] 2.1 `scripts/export_openapi.py` и `--check` в CI; `docs/openapi/openapi.json` вместо `openapi.yaml`
- [x] 2.2 `pyyaml` и `types-pyyaml` удалены — их больше никто не импортирует; `Dockerfile` без контракта; удалены `review-job.schema.json`, `error.schema.json`

## 3. Документы и проверки

- [x] 3.1 `AGENTS.md`, `.agents/rules/backend.md`, шаблон эндпоинта, `openspec/config.yaml`, `README.md`, `PIPELINE_SPEC.md` §9
- [x] 3.2 `tests/test_docs.py`, `tests/test_problem_details.py`; `ruff`, `lint-imports`, `mypy`, `pytest`, `export_openapi.py --check`, `openspec validate --all --strict`
