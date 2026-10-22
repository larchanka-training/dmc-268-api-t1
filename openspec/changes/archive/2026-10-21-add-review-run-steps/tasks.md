## 1. Домен

- [x] 1.1 `app/domain/enums.py`: `ReviewRunStage`, `ReviewRunStepStatus`; `app/domain/entities.py`: `ReviewRunStep`
- [x] 1.2 `app/domain/run_steps.py`: чистые `start_step`, `finish_step`, `error_code`; коды сбоев — атрибут `code` у `LlmError`, `VcsError` и наследников, новый `LlmTimeoutError`. Проверка: `tests/domain/test_run_steps.py`

## 2. Хранение

- [x] 2.1 Ревизия `0007_add_review_run_steps.py`: таблица `review_run_steps`, enum-типы, CHECK-ограничения, откат. Проверка: `tests/db/test_drift.py`, `tests/db/test_review_run_steps_migration.py`, `tests/db/test_constraints.py`, `uv run alembic heads`
- [x] 2.2 Порт `ReviewRunStepRepo`, адаптер `SqlAlchemyReviewRunStepRepo`, `UnitOfWork.review_run_steps`, фейк для тестов. Проверка: `tests/db/test_adapters.py` на настоящей базе

## 3. Воркер

- [x] 3.1 `LlmRequest.on_retry`; `complete_structured` и `FallbackLlmGateway` сообщают о повторе с токенами от начала вызова; адаптеры бросают `LlmTimeoutError` на таймаут. Проверка: `tests/infrastructure/test_llm_structured.py`, `test_fallback_gateway.py`, `test_ollama_gateway.py`, `test_openai_compatible_gateway.py`
- [x] 3.2 `fetch_review_diff` отдельно от `build_review_context`; `StepRecorder` пишет начало и исход каждого этапа в `run_review`, повтор — отдельным шагом, упавший шаг — с кодом и токенами. Проверка: `tests/application/test_review_pipeline.py`

## 4. API и контракт

- [x] 4.1 `GET /api/v1/reviews/{review_run_id}/steps` вместо `501`: шаги по порядку начала, `404 REVIEW_NOT_FOUND`, пустой список. Проверка: `tests/test_reviews_api.py`, `tests/test_problem_details.py`, `tests/api/test_contract_guards.py`
- [x] 4.2 Экспорт `docs/openapi/openapi.json` (`uv run python scripts/export_openapi.py`); схема `ReviewRunStepListResponse` не меняется

## 5. Документация и проверки

- [x] 5.1 `docs/erd.md`, `docs/BACKEND_ARCHITECTURE.md`
- [x] 5.2 `ruff check`, `lint-imports`, `mypy`, `pytest` с `TEST_DATABASE_URL`, `alembic heads`, `openspec validate --all --strict` проходят
