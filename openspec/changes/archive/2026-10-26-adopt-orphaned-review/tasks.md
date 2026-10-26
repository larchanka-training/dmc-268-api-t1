## 1. Красные тесты

- [x] 1.1 Chaos: воркер умер после записи ревью на GitHub, повторная доставка - `failed`, новый прогон не дублирует замечание (`tests/chaos/test_worker_crash_chaos.py`)
- [x] 1.2 Прикладной слой: сирота найдена - записи сводки и inline у упавшего прогона, находка нового покрыта; не найдена - публикация как обычно; поиск упал - warning, `completed` (`tests/application/test_publish_review.py`, `RecordingVcsGateway`)

## 2. Хранилище и порт

- [x] 2.1 `ReviewRunRepo.list_unpublished_failures(merge_request_id)`: SQL-адаптер с интеграционным тестом, `FakeUnitOfWork`
- [x] 2.2 Порт `VcsGateway.find_published_review`, адаптер `GitHubReviewClient.find_published_review` (`_find_review_by_marker` + `_map_fetched_inline_comments`) с тестом адаптера

## 3. Публикация

- [x] 3.1 `publish_review`: подбор сирот (`_adopt_orphaned_reviews`) до чтения тредов; общая запись публикации `_record_publication` и черновики `_inline_drafts`

## 4. Документация

- [x] 4.1 `docs/BACKEND_ARCHITECTURE.md` (`VcsGateway`), отчёт `docs/testing/SECURITY_AND_REGRESSION_REPORT.md`
