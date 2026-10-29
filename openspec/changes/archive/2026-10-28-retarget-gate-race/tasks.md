## 1. Красные тесты

- [x] 1.1 Приём: повтор после сдвига вершины ветки — дубль; A → B → упало → A и повтор после упавшего — новый прогон; `cancelled` последним — не дубль; порядок коммит → check → задача; сбой блокировки — `failed` и прежняя ветка, повтор снова блокирует (`tests/application/test_handle_webhook.py`)
- [x] 1.2 Гонка воркера и пересчёта со сменой ветки кончается `failure` или gate завершённого нового прогона (`test_publish_review.py`, `test_sync_thread_resolution.py`)
- [x] 1.3 Подбор: сверка текста (`tests/infrastructure/test_github_review_publish.py`), старые «сироты» не запрашиваются, параллельный подбор не роняет публикацию (`test_publish_review.py`, `tests/db/test_adapters.py`)

## 2. Реализация

- [x] 2.1 `handle_webhook_event`: `retarget` по ветке, `_duplicate_of` по `find_latest`, порядок и откат
- [x] 2.2 `settle_merge_gate` в `publish_review` и `refresh_merge_gate_for_merge_request`
- [x] 2.3 `match_body` в `_map_fetched_inline_comments`, граница в `list_unpublished_failures`, `_RACE_CONSTRAINTS`
