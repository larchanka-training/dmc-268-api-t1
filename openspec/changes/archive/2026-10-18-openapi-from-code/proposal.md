## Why

Контракт API писался руками в `docs/openapi/openapi.yaml`, а сервер отдавал этот файл по
`/openapi.json`. Код и контракт держала в согласии только сверка в `tests/test_docs.py` с
самописным валидатором JSON Schema, и сверялось не всё: типы и enum'ы — руками. Два
источника правды для одной схемы расходятся, а фронтенду, который генерирует типы, нужна
одна.

## What Changes

- Источник правды для контракта — код. Модели ошибок — pydantic: `Problem` и по модели на
  каждый код с константами `type`, `title`, `code`, `status` и примером; `ValidationProblem`
  с `errors`; объединение `AnyProblem` с discriminator по `code`. Тело ошибки сервер строит
  через эти модели.
- У каждой ручки в коде — её ответы ошибок моделями и `default` с `AnyProblem`; `422` описан
  `ValidationProblem`, а не `HTTPValidationError` FastAPI. Тип содержимого ошибок
  `application/problem+json` и заголовок `X-Trace-ID` схеме дописывает одно место.
- `/openapi.json` и `/docs` отдают схему из кода. `docs/openapi/openapi.yaml` удалён,
  `docs/openapi/openapi.json` — экспорт (`scripts/export_openapi.py`), CI проверяет, что он
  свежий (`--check`).
- Запланированная ручка — заглушка с полной схемой, отвечает `501` с новым кодом
  `NOT_IMPLEMENTED`; пометка `x-not-implemented` больше не нужна. Первая такая ручка —
  `POST /api/v1/reviews` (ручной запуск ревью): `CreateReviewRequest`, `202 ReviewJob`,
  `409 REVIEW_ALREADY_ACTIVE`, `422`; сессия и `Origin` — как у изменяющих ручек.
- `review-job.schema.json` и `error.schema.json` удалены: это были ручные копии схем
  `ReviewJob` и `ReviewError` из контракта, теперь они — модели в `app/api/reviews.py`.
  `finding.schema.json` остаётся — это контракт выхода модели, а не API.

## Impact

- Код: `app/api/problems.py` (новый), `app/api/errors.py`, `app/api/openapi.py` (вместо
  `openapi_contract.py`), `app/api/factory.py`, роуты `app/api/*`; `scripts/export_openapi.py`.
- Сборка: `pyyaml` удалён из зависимостей — его больше никто не импортирует, `Dockerfile` не копирует контракт, шаг
  `export_openapi.py --check` в CI.
- Документы: `AGENTS.md`, `.agents/rules/backend.md`, шаблон эндпоинта, `openspec/config.yaml`,
  `README.md`, `PIPELINE_SPEC.md` §9.
- Фронтенд: типы — из `docs/openapi/openapi.json`; пути в нём уже с `/api/v1`, `servers` нет;
  `operationId` — имя функции роута в camelCase.
- Тесты: `tests/test_docs.py` (экспорт свежий, каталог ошибок, `problem+json`, нет
  `HTTPValidationError`), `tests/test_problem_details.py` (тела через `AnyProblem`, `501`).
