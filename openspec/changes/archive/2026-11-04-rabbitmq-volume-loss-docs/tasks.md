## 1. Документация

- [x] 1.1 Сверить по коду, что сообщение DLQ не несёт данных сверх базы, кроме `installation_id`: поля `ReviewJob` против `review_runs`, `_mark_failed` до `nack`, `sweep_stale_runs` для прогонов без записи `failed`
- [x] 1.2 Сверить, что definitions воспроизводит деплой: `RABBITMQ_DEFAULT_USER` в `infra/rabbitmq.tf`, `passwords.sh`, `queue_declare` в `app/infrastructure/queue/rabbitmq.py`, политик в репозитории нет
- [x] 1.3 Абзац о потере тома брокера в `infra/README.md`, «Реплика и резервные копии»
