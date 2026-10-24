## 1. Приём без диффа

- [x] 1.1 Красный тест: `handle_webhook_event` не вызывает `fetch_diff` — у фейка VCS только запрос метаданных, исход `success` прежний (`tests/application/test_handle_webhook.py`, `tests/api/test_webhooks_e2e.py`)
- [x] 1.2 `handle_webhook_event`: убрать `vcs.fetch_diff` и рассуждение о раннем 502 из docstring и комментариев

## 2. Лимит диффа в воркере

- [x] 2.1 Красные тесты адаптера `GitHubVcsGateway.fetch_diff`: объявленная длина больше лимита и поток без длины — `VcsFileTooLargeError`, тело не дочитывается дальше лимита (`tests/infrastructure/test_github_vcs_gateway.py`)
- [x] 2.2 Красный тест сценария прогона: дифф больше лимита — прогон `failed` с причиной, шаг `fetching_diff` — `DIFF_TOO_LARGE`, модель не вызывается (`tests/application/test_review_pipeline.py`)
- [x] 2.3 Порт `VcsGateway.fetch_diff(..., *, max_bytes)`, адаптер — через потоковый `_get(..., max_bytes=…)`
- [x] 2.4 `DiffTooLargeError` (`app/domain/vcs_errors.py`, код `DIFF_TOO_LARGE`), `fetch_review_diff` переводит в него `VcsFileTooLargeError`; `DIFF_TOO_LARGE` — в `ReviewErrorCode`
- [x] 2.5 Настройка `diff_max_bytes` (`DIFF_MAX_BYTES`, 5 МиБ), `ContextLimits.diff_max_bytes`, проводка в `app/worker/factory.py`
- [x] 2.6 `publish_review`: инкрементальный дифф тредов с тем же лимитом, превышение — изменение строки якоря не подтверждено

## 3. Комментарии, контракт, документация

- [x] 3.1 Комментарии «хостинг повторит доставку» в `handle_webhook.py`, `app/api/webhooks.py` и тестах: GitHub неудачные доставки не повторяет, переотправка — #149
- [x] 3.2 `description` ручки `POST /api/v1/webhooks/{provider}`; `uv run python scripts/export_openapi.py`
- [x] 3.3 `docs/pipeline/PIPELINE_SPEC.md`, `docs/BACKEND_ARCHITECTURE.md`, `README.md`, `.env.example`
