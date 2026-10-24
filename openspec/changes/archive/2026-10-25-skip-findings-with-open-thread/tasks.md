## 1. Состояние тредов на хостинге

- [x] 1.1 Порт `VcsGateway.fetch_review_threads_state` → `ReviewThreadState(resolved, line)` вместо `fetch_review_threads_resolved_state`; адаптер `GitHubReviewClient` читает `isOutdated` и `line` из того же запроса `reviewThreads`, `_thread_current_line` (тест `tests/infrastructure/test_github_review_publish.py`)
- [x] 1.2 Фейки: `RecordingVcsGateway.thread_states`, `FakeGitHub.outdated_threads` с `isOutdated`/`line` в ответе GraphQL

## 2. Модель данных

- [x] 2.1 Миграция `0012`: `findings.covered_by_finding_id` (FK RESTRICT, `ck_findings_covered_by_other`), `published_comments.thread_resolved_by_bot`
- [x] 2.2 Сущности `Finding.covered_by_finding_id`, `PublishedComment.thread_resolved_by_bot`, `BotThread`; репозитории `FindingRepo.mark_covered`, `PublishedCommentRepo.list_threads_for_merge_request` (тест `tests/db/test_adapters.py`)
- [x] 2.3 `docs/erd.md`

## 3. Решение и публикация

- [x] 3.1 Красные тесты `publish_review`: тот же finding на открытом треде — 0 inline; ставший `critical` — публикуется; устаревший тред не подавляет; тред на новой строке совпадает по текущей строке; закрытый человеком — не публикуется, `critical` после повышения — публикуется; закрытый ботом — публикуется снова; одна сверка на прогон; сбой сверки — публикуется всё (`tests/application/test_publish_review.py`)
- [x] 3.2 Чистая функция `thread_coverage` (`app/domain/thread_coverage.py`, тест `tests/domain/test_thread_coverage.py`)
- [x] 3.3 `publish_review`: сверка `_sync_threads_with_host` до публикации, `record_threads_resolved_on_host` общий со `sync_open_threads_from_host`, `mark_covered` в транзакции записи публикации, повторная сверка после публикации убрана
- [x] 3.4 `_publish_thread_follow_ups`: без ответа в вытесненный тред, без закрытия треда, покрывающего находку; отметка `thread_resolved_by_bot` при закрытии ботом
- [x] 3.5 Вебхук `pull_request_review_thread` снимает отметку бота при смене состояния (`tests/application/test_sync_thread_resolution.py`)
- [x] 3.6 Сквозные тесты: второй push с той же находкой, тред закрыт человеком, устаревший тред, сбой GraphQL на повторном прогоне (`tests/e2e/test_thread_dedup_e2e.py`)
- [x] 3.7 Миграция 0012: треды, закрытые до неё, — закрытые ботом (`tests/db/test_thread_coverage_migration.py`)

## 4. Контракт API

- [x] 4.1 `FindingResponse.covered_by_finding_id` (тест `tests/test_reviews_api.py`)
- [x] 4.2 `uv run python scripts/export_openapi.py`
