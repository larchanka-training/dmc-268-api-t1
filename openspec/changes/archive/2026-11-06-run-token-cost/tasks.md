Каждая задача: красно-зелёный виток (скилл `backend-tdd`).

## 1. Расход в домене и порте

- [x] 1.1 `LlmUsage` со сложением, нулём и `total_tokens` (`app/domain/llm_usage.py`). Тест: `tests/domain/test_llm_usage.py`
- [x] 1.2 `LlmMeter` и поле `meter` у `LlmRequest`; убрать `LlmResponse.tokens_used`, `LlmError.tokens_used`, токены из `RetryListener` (`app/application/ports/llm_gateway.py`, `app/domain/llm_errors.py`)

## 2. Инфраструктура модели

- [x] 2.1 `complete_structured` пишет `RawReply.usage` в счётчик на каждый ответ, `RawReply.rejected` — после записи (`app/infrastructure/llm/structured.py`). Тест: `tests/infrastructure/test_llm_structured.py` — повтор по схеме, сбой после ответа
- [x] 2.2 `FallbackLlmGateway` без суммы потраченного и сдвига слушателя (`app/infrastructure/llm/fallback.py`). Тест: `tests/infrastructure/test_fallback_gateway.py` — фоллбэк учитывается один раз
- [x] 2.3 OpenAI-совместимый адаптер разбирает `usage` (итог провайдера, поля без значения — 0) и точную стоимость через `parse_float=Decimal` (`openai_compatible.py`, `token_count` в `structured.py`). Тест: `tests/infrastructure/test_openai_compatible_gateway.py` — точное 0.0000913, 10-й знак, без стоимости, не в EUR, мусор, `null`, смена ключа
- [x] 2.4 Ollama: токены раздельно, отказ по окну — `rejected` (`ollama.py`). Тест: `tests/infrastructure/test_ollama_gateway.py` — разбор, расход отвергнутого ответа учтён

## 3. Пайплайн

- [x] 3.1 Счётчик на прогон; `StepRecorder` считает долю шага по счётчику; убрать `unrecorded_tokens`, `spent_tokens`, токены в `LlmReviewResult`/`VerificationAnswer` (`review_pipeline.py`, `review_run_steps.py`, `llm_review.py`, `llm_schema.py`). Тест: `tests/application/test_review_pipeline.py` — повтор по схеме суммирует токены и стоимость, сбой сохраняет расход

- [x] 3.2 Отменённый прогон сохраняет расход: `ReviewRunRepo.record_cancelled_spend`, вызов в ветке отмены `run_review`. Тест: `tests/application/test_review_pipeline.py` — `test_a_run_superseded_after_the_model_answered_keeps_its_spend`; `tests/db/test_adapters.py` — статус не меняется, не `cancelled` — ничего

## 4. Хранение и API

- [x] 4.1 `ReviewRun.usage`, колонки `review_runs` (`cost_eur NUMERIC(20, 10)`), маппер и репозиторий; миграция `0018` с откатом (`app/infrastructure/db/*`, `alembic/versions/0018_add_review_run_usage.py`). Тест: `tests/db/test_adapters.py` — прогон читается с разбивкой и всеми 10 знаками; `tests/e2e/test_run_cost_e2e.py` — стоимость из тела ответа доходит до колонки и суммы SQL точно
- [x] 4.2 Поле `usage` в `ReviewRunResponse` (`app/api/reviews.py`). Тест: `tests/test_reviews_api.py` — разбивка, `cost_eur` точной строкой без хвостовых нулей и экспоненты, `null` до анализа
- [x] 4.3 `uv run python scripts/export_openapi.py`; `docs/erd.md`

## 5. Проверки

- [x] 5.1 `uv run ruff check .`, `uv run lint-imports`, `uv run mypy .`, `uv run alembic heads`, `PYTEST_FAIL_ON_SKIP=1 uv run pytest`, `npx @fission-ai/openspec@1.15.0 validate --all --strict`
