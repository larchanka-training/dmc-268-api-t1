Каждая задача с кодом — красно-зелёный виток (скилл `backend-tdd`): сначала тест в названном
файле падает, потом код, потом тест зелёный. Решения — `design.md` (D1–D4), поведение — дельта
`specs/webhook-intake/spec.md`.

## 1. Спека

- [x] 1.1 Change `worker-diff-line-limit`: `proposal.md`, `design.md` (проверка до разбора, константа, значение и замер памяти воркера), дельта `webhook-intake` («Лимит размера диффа в воркере»). Проверка: `npx @fission-ai/openspec@1.15.0 validate --all --strict`

## 2. Лимит строк в воркере

- [x] 2.1 Константа `REVIEW_DIFF_MAX_LINES` (первой версией 50 000, после ревью — 5.2) и проверка `raw.count("\n") > REVIEW_DIFF_MAX_LINES` → `DiffTooLargeError` в `fetch_review_diff` после `fetch_diff` и до `parse_diff` (`app/application/use_cases/build_review_context.py`); docstring модуля и функции называют отказ. Тест: `tests/application/test_build_review_context.py` — предел + 1 строка → `DiffTooLargeError`; ровно предел → дифф отдаётся как есть
- [x] 2.2 Конвейер: дифф больше `REVIEW_DIFF_MAX_LINES` строк → прогон `failed`, шаг `fetching_diff` — `failed` с `DIFF_TOO_LARGE`, модель не вызывается, на PR ничего, пакет контекста не записан. Тест: `tests/application/test_review_pipeline.py`; помощник `diff_of_lines` — в `tests/fakes/webhooks.py`

## 3. Документация

- [x] 3.1 Предел строк (`REVIEW_DIFF_MAX_LINES`, константа, проверка до разбора) рядом с `diff_max_bytes`: «Исчерпание ресурсов» в `docs/BACKEND_ARCHITECTURE.md`, комментарий над `DIFF_MAX_BYTES` в `.env.example`. Проверка: `uv run pytest tests/test_docs.py`

## 4. Синхронизация и проверки

- [x] 4.1 Статические проверки и полный прогон: `uv run ruff check .`, `uv run lint-imports`, `uv run python scripts/export_openapi.py --check`, `uv run alembic heads` — одна голова; `PYTEST_FAIL_ON_SKIP=1 uv run pytest` с `TEST_DATABASE_URL`, `TEST_RABBITMQ_URL`, `TEST_RABBITMQ_CONTAINER` — без пропусков
- [x] 4.2 Архив: `openspec archive worker-diff-line-limit` синхронизирует `openspec/specs/`. Проверка: `npx @fission-ai/openspec@1.15.0 validate --all --strict`

## 5. Замечания ревью #328

- [x] 5.1 Ханк длиннее остатка бюджета — omission `hunk`/`budget` до `_hunk_item`, без `sanitize` (`app/application/context_assembly.py`, design D4). Тест: `tests/application/test_context_assembly.py` — ханк из 1 млн `\x01` уходит в `omitted`, пик `tracemalloc` меньше длины текста
- [x] 5.2 `REVIEW_DIFF_MAX_LINES = 200_000` по замеру памяти воркера в контейнере 512 МБ (design D3); дельта `webhook-intake`, `.env.example`, `docs/BACKEND_ARCHITECTURE.md`. Тест: граница в `tests/application/test_build_review_context.py` (имя без «before_parsing»), строки lock-файла считаются
- [x] 5.3 Лимит строк назван рядом с байтовым: `docs/pipeline/PIPELINE_SPEC.md` (`DIFF_TOO_LARGE`), docstring `DiffTooLargeError`, комментарий над `diff_max_bytes` в `app/config.py`
- [x] 5.4 Проверки из 4.1 и повторный архив: `openspec archive worker-diff-line-limit`. Проверка: `npx @fission-ai/openspec@1.15.0 validate --all --strict`
