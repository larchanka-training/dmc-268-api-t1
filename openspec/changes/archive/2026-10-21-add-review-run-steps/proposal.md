## Why

«Ход прогона» в кабинете показывает шаги воркера, но ручка `GET /api/v1/reviews/{review_run_id}/steps` — заглушка `501` (форма ответа `ReviewRunStepListResponse` в контракте с #83). Шаги — данные продукта: их источник — база, а не логи и не телеметрия, и записать их может только воркер, проводящий прогон (задача larchanka-training/dmc-268-api-t1#88).

## What Changes

- Таблица `review_run_steps` (миграция `0007`): этап (`fetching_diff`, `building_context`, `analysing`, `verifying`, `publishing`), статус (`running`, `completed`, `failed`), начало, длительность, токены, код ошибки. Запросов и ответов модели в ней нет.
- Порт `ReviewRunStepRepo` и адаптер `SqlAlchemyReviewRunStepRepo`; `UnitOfWork.review_run_steps`.
- Воркер пишет начало и исход каждого этапа (`StepRecorder`), включая упавший. Получение диффа — отдельный шаг `fetching_diff` до сборки контекста, верификация — шаг `verifying` после ревью.
- Повтор этапа — отдельный шаг: адаптер модели сообщает о каждом повторе внутри вызова (ответ не по схеме, переход к следующему провайдеру) слушателю `LlmRequest.on_retry`; упавшая попытка остаётся `failed` со своим кодом и токенами.
- Код ошибки шага — из каталога ошибок прогона (§8 `PIPELINE_SPEC.md`), его несут классы сбоев: `LlmError.code`, `VcsError.code`; таймаут модели — отдельный класс `LlmTimeoutError` (`LLM_TIMEOUT`). Прочие исключения — `INTERNAL_ERROR`.
- `GET /api/v1/reviews/{review_run_id}/steps` вместо `501`: шаги по порядку начала, `404 REVIEW_NOT_FOUND`, если прогона нет, пустой `items` у прогона без шагов. Схема ответа не меняется.
- Событие `step` в SSE — вне объёма.

## Impact

- Код: `app/domain/{enums,entities,run_steps,llm_errors,vcs_errors}.py`, `app/application/{review_pipeline,review_run_steps,llm_review}.py`, `app/application/use_cases/build_review_context.py`, `app/application/ports/{llm_gateway,repositories,unit_of_work}.py`, `app/infrastructure/db/*`, `app/infrastructure/llm/{structured,fallback,ollama,openai_compatible}.py`, `app/infrastructure/vcs/errors.py`, `app/api/reviews.py`.
- База: ревизия `alembic/versions/0007_add_review_run_steps.py` с откатом; два новых enum-типа.
- Контракт: `docs/openapi/openapi.json` — у `/steps` нет ответа `501` и обновлено описание; схемы не меняются.
- Документация: `docs/erd.md`, `docs/BACKEND_ARCHITECTURE.md`.
