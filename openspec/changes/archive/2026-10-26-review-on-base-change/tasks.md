## 1. Тесты

- [x] 1.1 Домен: `edited` с `changes.base` — событие ревью, без него — `None` (`tests/domain/test_webhook.py`)
- [x] 1.2 Приём: смена ветки — отмена, новый прогон, блокировка и задача в этом порядке; сбой блокировки — 502, `failed` и прежняя ветка; повтор после сдвига вершины — дубль; после упавшего или отменённого — новый прогон (`tests/application/test_handle_webhook.py`)
- [x] 1.3 Gate: гонка публикации и пересчёта со сменой ветки кончается блокировкой или итогом нового прогона; повтор до трёх раз; упавший последний прогон — «Ревью не удалось» (`test_publish_review.py`, `test_sync_thread_resolution.py`, `test_review_pipeline.py`)
- [x] 1.4 Отменённый прогон: не публикует, шаг брошен, сообщение подтверждено (`test_publish_review.py`, `test_review_pipeline.py`)
- [x] 1.5 Сквозной: обход merge gate сменой целевой ветки закрыт (`tests/security/test_pr_manipulation_e2e.py`)

## 2. Реализация

- [x] 2.1 `extract_github_event`: `edited` с `_base_changed`; `handle_webhook_event`: `retarget` по ветке, `_duplicate_of` по `find_latest`, `_supersede`, порядок и откат
- [x] 2.2 `ReviewRunRepo.find_latest`: SQL с интеграционным тестом, фейк
- [x] 2.3 `settle_merge_gate` со своим check — в `publish_review` и `refresh_merge_gate_for_merge_request`; `build_retarget_check`, `build_failed_review_check`, `_block_head_of_failed_run`
- [x] 2.4 `RunCancelledError` в `publish_review`, `StepRecorder.abandon` и ack отменённого прогона в `run_review`
