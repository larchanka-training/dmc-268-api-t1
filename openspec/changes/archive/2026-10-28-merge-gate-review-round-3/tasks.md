## 1. Красные тесты

- [x] 1.1 Подбор «сироты», которую выметатель перевёл в `failed` после чужой публикации (`tests/application/test_publish_review.py`, `tests/db/test_adapters.py`)
- [x] 1.2 Повтор поправки gate по текущему прогону, отказ после трёх попыток, отменённый последний прогон (`test_publish_review.py`)
- [x] 1.3 `failure` упавшего прогона после прежнего ревью head; без прежнего и за более поздним — ничего (`tests/application/test_review_pipeline.py`)

## 2. Реализация

- [x] 2.1 `list_unpublished_failures` по `last_progress_at` и `published_at`
- [x] 2.2 `SETTLE_ATTEMPTS` в `settle_merge_gate`, `build_failed_review_check`, `_block_head_of_failed_run`
