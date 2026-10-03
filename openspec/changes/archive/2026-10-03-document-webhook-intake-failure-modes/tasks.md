## 1. Дельта спеки

- [x] 1.1 MODIFIED «Повторная доставка на тот же коммит не создаёт второй
  активный прогон»: 202 с проекцией существующего прогона, исходы гонки
  (spec: webhook-intake)
- [x] 1.2 ADDED «Сбой постановки задачи переводит прогон в failed»: failed
  с причиной, 5xx, повторная доставка создаёт новый прогон (spec:
  webhook-intake)
- [x] 1.3 ADDED «Неподдержанный провайдер получает 501» (spec:
  webhook-intake)
- [x] 1.4 ADDED «Публикация переживает простой соединения»: переоткрытие и
  один повтор, второй отказ — наверх (spec: webhook-intake; при ребейзе #36
  переезжает в `job-queue`)

## 2. Сверка с реализацией (поведение уже в PR #37)

- [x] 2.1 Сбой постановки: `test_broker_failure_fails_the_run_and_next_delivery_starts_a_new_one`
  и `test_broker_failure_does_not_commit_the_run` — `tests/application/test_handle_webhook.py`
- [x] 2.2 Дубли и гонки: `test_duplicate_delivery_returns_existing_run_and_enqueues_nothing`,
  `test_lost_race_returns_survivor_run`, `test_race_with_a_different_commit_retries_the_write`
- [x] 2.3 501: тесты неподдержанного провайдера — `tests/api/test_webhooks.py`
- [x] 2.4 Переоткрытие соединения: `tests/queue/test_channel_liveness.py` —
  простоявшее соединение, двойной отказ, закрытый канал

## 3. Синхронизация и архивация

- [x] 3.1 Дельта синхронизирована в `openspec/specs/webhook-intake/spec.md`
- [x] 3.2 Change заархивирован в `openspec/changes/archive/`
- [x] 3.3 `uv run openspec validate --strict` green; `uv run ruff check .`,
  `uv run lint-imports`, `uv run pytest` — green
