Каждая задача с кодом — красно-зелёный виток (скилл `backend-tdd`): сначала тест в названном
файле падает, потом код, потом тест зелёный. Решения — `design.md` (D1–D4), поведение — дельты
`specs/review-rest-api/spec.md` и `specs/webhook-intake/spec.md`.

## 1. Спека

- [x] 1.1 Change `run-diff-line-limit`: `proposal.md`, `design.md` (замер GitHub, лимит строк, константа, оценка кэша), дельты `review-rest-api` («Отказы диффа прогона») и `webhook-intake` («Лимит размера диффа в воркере»). Проверка: `npx @fission-ai/openspec@1.15.0 validate --all --strict`

## 2. Лимит строк в use case

- [x] 2.1 Константа `RUN_DIFF_MAX_LINES = 50_000` и проверка `raw.count("\n") > RUN_DIFF_MAX_LINES` → `DiffTooLargeError` после `fetch_diff` и до `parse_diff`, без записи в кэш (`app/application/use_cases/run_diff.py`); docstring модуля и `load_run_diff` называют отказ. Тест: `tests/application/test_run_diff.py` — 50 001 строка → `DiffTooLargeError`, запись в кэше по ключу прогона отсутствует; ровно 50 000 строк → представление построено и записано в кэш

## 3. HTTP API

- [x] 3.1 `detail` для `DiffTooLargeError` в `app/api/reviews.py` называет обе границы (`RUN_DIFF_MAX_BYTES` байт или 50 000 строк) и отказ GitHub; `_DIFF_DESCRIPTION`, `DiffTooLargeProblem` (`app/api/problems.py`) и словарь `ERRORS_*` (`app/api/errors.py`) — о строках и `422` GitHub. Тест: `tests/test_reviews_api.py` — дифф `FakeVcs` больше 50 000 строк → `422 DIFF_TOO_LARGE`
- [x] 3.2 Экспорт контракта: `uv run python scripts/export_openapi.py`, `docs/openapi/openapi.json` — в том же коммите. Проверка: `uv run python scripts/export_openapi.py --check`, `uv run pytest tests/test_docs.py tests/api/test_contract_guards.py`

## 4. Адаптер GitHub

- [x] 4.1 `GitHubVcsGateway._request` принимает `too_large_statuses`; статус из набора → `VcsFileTooLargeError`; `fetch_diff` передаёт `{HTTPStatus.UNPROCESSABLE_CONTENT}`, `406` не добавляется (`app/infrastructure/vcs/github.py`). Тест: `tests/infrastructure/test_github_vcs_gateway.py` — `422` с телом GitHub на compare → `VcsFileTooLargeError` без повторов; прочий `4xx` — по-прежнему `VcsError`

## 5. Документация

- [x] 5.1 Оценка кэша диффов — ≈ 48 МиБ ASCII, ≈ 96 МиБ с кириллицей, до ≈ 192 МиБ с символом вне BMP (PEP 393): комментарий `shortcut:` у `_run_diff_cache` (`app/infrastructure/container.py`), `docs/SYSTEM_DESIGN.md` §4.3, строка `CacheStore` в `docs/BACKEND_ARCHITECTURE.md`; там же лимит 50 000 строк и `422` GitHub → `DIFF_TOO_LARGE`. Проверка: `uv run pytest tests/test_docs.py`

## 6. Синхронизация и проверки

- [x] 6.1 Статические проверки и полный прогон: `uv run ruff check .`, `uv run lint-imports`, `uv run python scripts/export_openapi.py --check`, `uv run alembic heads` — одна голова; `PYTEST_FAIL_ON_SKIP=1 uv run pytest` с `TEST_DATABASE_URL`, `TEST_RABBITMQ_URL`, `TEST_RABBITMQ_CONTAINER` — без пропусков
- [x] 6.2 Архив: `openspec archive run-diff-line-limit` синхронизирует `openspec/specs/`. Проверка: `npx @fission-ai/openspec@1.15.0 validate --all --strict`
