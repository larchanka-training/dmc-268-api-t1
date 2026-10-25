## Why

Ответы в треды (`_publish_thread_follow_ups`) и сверка тредов с GitHub (`sync_open_threads_from_host`) идут в конце `publish_review`, когда ревью уже в PR. Если GitHub не отвечает (например, GraphQL — 502 после трёх попыток адаптера), прогон уходит в `failed` на этапе `publishing`, check run merge gate не выставляется, сообщение уходит в DLQ. В PR есть ревью, но нет статуса, а повтор после `failed` опубликует второе ревью. Задача #76, воспроизведение — `docs/testing/RULES_E2E_REPORT.md` §5.3.

## What Changes

- Ответы в треды (`_publish_thread_follow_ups`) — best-effort по каждому треду: сбой VCS на инкрементальном диффе, ответе или закрытии треда пишется warning'ом с id прогона и корневым `provider_comment_id` и не мешает следующим тредам, сверке и gate. Ответ записывается в `published_comments` своей транзакцией сразу после публикации, до закрытия треда; отметка `thread_resolved` — отдельной транзакцией после успешного закрытия. Без инкрементального диффа ответ публикуется, критичный тред не закрывается — как при превышении лимита.
- Сверка тредов после публикации — best-effort: сбой VCS (`VcsError` и наследники) пишется в лог warning'ом с id прогона и PR, прогон завершается `completed`.
- Check run merge gate выставляется всегда после попытки сверки; если она упала — по данным в базе. Порядок прежний: gate после сверки, иначе он видел бы устаревшие треды.
- Ошибки кода (не `VcsError`) по-прежнему роняют прогон.
- Повтора и второго ревью нет: треды досинхронизирует следующий прогон или вебхук закрытия треда.

## Capabilities

### Modified Capabilities

- `review-pipeline`: MODIFIED «Merge gate по критичным открытым тредам» — сбой ответов в треды и сверки не отменяет gate и не роняет прогон; сценарий «GraphQL недоступен после публикации».
- `review-pipeline`: MODIFIED «Диалог в тредах на повторном прогоне» — ответ записывается до закрытия треда, сбой одного треда не мешает остальным; сценарии «Закрытие треда упало после ответа» и «Ответ в один тред не опубликован».

## Impact

- Код: `app/application/publish_review.py`.
- Тесты: `tests/application/test_publish_review.py`, сквозной сценарий в `tests/e2e/test_rules_e2e.py`, фейки `RecordingVcsGateway.threads_error`, `diff_error`, `reply_errors`, `resolve_errors` и `FakeGitHub.graphql_status`.
- Документация: `docs/testing/RULES_E2E_REPORT.md` §5.3.
- Схема базы, контракт API и настройки не меняются.
