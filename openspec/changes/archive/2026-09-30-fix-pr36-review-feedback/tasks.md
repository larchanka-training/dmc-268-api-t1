## 1. Приём вебхука

- [x] 1.1 `app/domain/webhook_signature.py`: чистая `verify_webhook_signature`,
      сравнение байтов; тест `tests/domain/test_webhook_signature.py` (включая
      не-ASCII заголовок).
- [x] 1.2 `app/config.py`: `github_webhook_secret` с `min_length=1`,
      `stale_run_timeout_seconds` (по умолчанию 1800, `> 0`);
      `tests/test_config.py`.
- [x] 1.3 `app/api/webhooks.py`: `commit`, затем `enqueue`; при сбое очереди
      прогон в `failed` и проброс ошибки; обновление `state`, `title`, веток
      запроса на изменение. Тесты в `tests/test_webhooks.py`: порядок,
      сбой очереди и повторная доставка, не-ASCII подпись, смерженный PR.

## 2. Пайплайн

- [x] 2.1 `FindingRepo.add_validated -> bool` (порт, SQL-адаптер, фейк);
      `FindingRepo.list_for_run` — `ORDER BY created_at, id`.
- [x] 2.2 `app/application/review_pipeline.py`: `now` и `monotonic` —
      функции; `_mark_failed` по свежей строке; проброс исходного исключения;
      `LookupError` для сообщения без прогона.
- [x] 2.3 `app/application/stale_sweep.py`: `sweep_stale_runs` поверх
      `find_stale`; `tests/application/test_stale_sweep.py`.
- [x] 2.4 Тесты `tests/application/test_review_pipeline.py`: `last_progress_at`
      по часам, точный `duration_seconds`, отклонённая привязка, сбой при
      записи `failed`, терминальная строка.

## 3. Воркер и очередь

- [x] 3.1 `app/worker/factory.py`: `heartbeat=0`, периодический `sweep` с
      защитой от сбоя; `tests/worker/test_worker_loop.py`.
- [x] 3.2 `app/infrastructure/queue/rabbitmq.py`: проверка живости канала;
      `tests/queue/test_channel_liveness.py`.

## 4. Тестовая инфраструктура

- [x] 4.1 `tests/fakes/unit_of_work.py`: транзакционный фейк (откат к
      последнему коммиту), `update` и сортировка как у SQL-адаптеров.

## 5. Финальная проверка

- [x] 5.1 `uv run ruff check . && uv run mypy . && uv run lint-imports`
      — зелено. С `TEST_DATABASE_URL` и `TEST_RABBITMQ_URL` против живых
      `postgres:18-alpine` и `rabbitmq:3.13-alpine`: 180 passed, 0 skipped.
