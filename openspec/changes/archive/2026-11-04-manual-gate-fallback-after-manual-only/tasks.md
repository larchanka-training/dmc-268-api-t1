Каждая задача — красно-зелёный виток (скилл `backend-tdd`). Решения — `design.md` (D1).

## 1. Правило подстановки

- [x] 1.1 `head_run_in_charge` подставляет последний завершённый ручной прогон, только если последний прогон на head — ручной и `failed` или `cancelled` (`app/application/merge_gate_publish.py`, D1). Тест: `tests/application/test_merge_gate_publish.py` — `webhook-after-manual`: завершённый вебхуковый прогон после завершённого ручного на паузе → вебхуковый, check паузы; `failed-webhook-after-manual`: упавший вебхуковый после завершённого ручного на паузе → упавший вебхуковый, check паузы
- [x] 1.2 `head_run_in_charge` принимает `Repository | None`, при `None` возвращает `find_latest` (D1). Тест: `tests/application/test_merge_gate_publish.py` — repository `None` → последний прогон на head

## 2. Места вызова и документация

- [x] 2.1 Четыре места вызова без ветвления на `repository is None`: `publish_pause_check` и `settle_merge_gate` (`app/application/merge_gate_publish.py`), `_block_head_of_failed_run` (`app/application/review_pipeline.py`), сверка gate после вебхука треда (`app/application/sync_thread_resolution.py`); прежние тесты зелёные
- [x] 2.2 Докстринги `head_run_in_charge`, `pause_check` и мест вызова описывают уточнённое условие и ссылаются на этот change

## 3. Проверки

- [x] 3.1 `uv run ruff check .`, `uv run lint-imports`, `uv run pytest`, `npx @fission-ai/openspec@1.15.0 validate --all --strict`
