## 1. Дедуп повтора после завершения

- [x] 1.1 Красные тесты `handle_webhook_event`: повтор после `completed` — исход `duplicate` с завершённым прогоном, задачи нет; после `failed` — новый прогон; завершённый `manual` дублем не делает (`tests/application/test_handle_webhook.py`)
- [x] 1.2 Красный тест адаптера `SqlAlchemyReviewRunRepo.find_reviewed` и приёма на настоящей базе (`tests/db/test_webhook_idempotency.py`)
- [x] 1.3 `REVIEWED_STATUSES` в `app/domain/enums.py`, `find_reviewed` в порте, адаптере и фейке, проверка в `handle_webhook_event`
- [x] 1.4 Сквозной тест: повтор после завершения прогона — один прогон, одно ревью (`tests/e2e/test_rules_e2e.py`)

## 2. Решение о переотправке

- [x] 2.1 Красные тесты чистой `select_redeliveries` (`tests/domain/test_webhook_redelivery.py`)
- [x] 2.2 `WebhookDelivery`, `RedeliveryPolicy`, `select_redeliveries` в `app/domain/webhook_redelivery.py`

## 3. Адаптер и сценарий

- [x] 3.1 Красные тесты адаптера `GitHubWebhookDeliveries` на `httpx.MockTransport`: JWT в заголовке, страницы по `Link`, стоп по `since`, переотправка, отказы (`tests/infrastructure/test_github_webhook_deliveries.py`)
- [x] 3.2 Порт `WebhookDeliveries` (`app/application/ports/webhook_deliveries.py`) и адаптер `app/infrastructure/vcs/github_deliveries.py`
- [x] 3.3 Красные тесты сценария `redeliver_failed_webhooks` на фейке порта: переотправка только выбранных, лимит прерывает проход, прочий отказ — нет (`tests/application/test_webhook_redelivery.py`)
- [x] 3.4 Сценарий `redeliver_failed_webhooks` (`app/application/webhook_redelivery.py`)
- [x] 3.5 Красные тесты: устаревшая доставка `pull_request` (голова PR другая) не переотправляется, `pull_request_review_thread` — без сверки, сбои payload и метаданных — как у переотправки, бюджет времени прохода на подменённых часах; адаптерный тест `GET /app/hook/deliveries/{id}`
- [x] 3.6 `WebhookDeliveries.delivery_payload`, сверка головы через `VcsGateway.fetch_pr_metadata`, бюджет `WEBHOOK_REDELIVERY_PASS_BUDGET_SECONDS`
- [x] 3.7 Красный тест: 11 устаревших старых кандидатов и один свежий при лимите 10 — свежий переотправлен в первом проходе, во втором устаревшие не запрашиваются; `select_redeliveries` отдаёт всех подходящих, лимит — по переотправкам, память `guid` в замыкании `build_redelivery`

## 4. Проводка, настройки, контракт

- [x] 4.1 Красные тесты: шаг повтора в `build_maintenance` со своим интервалом и изоляцией сбоя; без реквизитов App шаг не собирается и пишет INFO (`tests/worker/test_maintenance.py`)
- [x] 4.2 Настройки `webhook_redelivery_*` в `app/config.py`, адаптер в контейнере, проводка в `app/worker/factory.py`, `.env.example`, `docs/BACKEND_ARCHITECTURE.md`
- [x] 4.3 `description` ручки `POST /api/v1/webhooks/{provider}`; `uv run python scripts/export_openapi.py`
- [x] 4.1 Purpose спек `webhook-intake`, `review-pipeline`, `review-rest-api` обновлён прямой правкой: архив переносит только требования, Purpose существующей спеки OpenSpec велит править в `openspec/specs/` напрямую; исключение записано в AGENTS.md
