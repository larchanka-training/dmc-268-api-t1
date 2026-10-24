## Why

GitHub неудачные доставки вебхука сам не повторяет («GitHub does not automatically redeliver failed deliveries»), переотправить их можно только в пределах трёх дней. Любой наш не-2xx на доставку — сбой постановки в очередь (`_fail_unenqueued` → 5xx), 502 VCS, ошибка базы, деплой — значит, что ревью этого push'а потеряно навсегда. Включить автоматический повтор без дедупа нельзя: дедуп сейчас только по активному прогону (`find_active`), и повтор после завершения прогона публикует второе ревью (#75). Задача #149, закрывает и #75.

## What Changes

- Дедуп без новой таблицы: у запроса на изменение есть завершённый не-`failed` прогон с источником `webhook` на тот же head-коммит — доставка дубль, ответ 202 с проекцией этого прогона, как сегодня у `duplicate`. `failed` прогон дублем не делает: повтор после сбоя постановки создаёт новый прогон. Таблицу ключей `X-GitHub-Delivery` не заводим: повтор после нашего сбоя приходит с тем же ключом и был бы отброшен.
- Повтор упавших доставок в периодическом проходе воркера: `GET /app/hook/deliveries` (JWT приложения) за трое суток, группировка по `guid`, переотправка `POST /app/hook/deliveries/{id}/attempts` для групп без успешной попытки и только для событий, которые обрабатывает приём. Устаревшая доставка `pull_request` (её коммит — уже не голова PR по `fetch_pr_metadata`) не переотправляется. Не больше N переотправок на `guid`, пауза растёт вдвое, за проход — ограниченное число страниц и переотправок и бюджет времени, запросы последовательно.
- Настройки `WEBHOOK_REDELIVERY_ENABLED`, `WEBHOOK_REDELIVERY_INTERVAL_SECONDS`, `WEBHOOK_REDELIVERY_MAX_ATTEMPTS`, `WEBHOOK_REDELIVERY_BACKOFF_SECONDS`, `WEBHOOK_REDELIVERY_MAX_PER_PASS`, `WEBHOOK_REDELIVERY_PASS_BUDGET_SECONDS`. Без реквизитов GitHub App повтор — no-op с одной строкой INFO при старте.

## Capabilities

### Modified Capabilities

- `webhook-intake`: MODIFIED «Повторная доставка отвечает существующим прогоном» — дубль и после завершённого не-`failed` прогона; ADDED «Упавшие доставки повторяются» и «Повтор доставок ограничен».
- `review-data-model`: MODIFIED «Один прогон на коммит, если не запрошено повторное ревью» — хранилище по-прежнему не блокирует прогон после терминального, но повтор доставки после `completed` до него не доходит.

## Impact

- Код: `app/application/use_cases/handle_webhook.py`, `ReviewRunRepository.find_reviewed` (порт, адаптер SQLAlchemy, фейк), чистая `select_redeliveries` в `app/domain/webhook_redelivery.py`, порт `WebhookDeliveries`, адаптер `GitHubWebhookDeliveries`, сценарий `redeliver_failed_webhooks`, проводка в `app/worker/factory.py`, настройки в `app/config.py`.
- Схема базы не меняется, миграции нет.
- Контракт API: меняется `description` ручки `POST /api/v1/webhooks/{provider}` — `docs/openapi/openapi.json` перегенерируется.
- Документация: `.env.example`, `docs/BACKEND_ARCHITECTURE.md`.
