## 1. Сверка тредов — best-effort

- [x] 1.1 Красный тест `publish_review`: `fetch_review_threads_resolved_state` бросает `VcsError` / `VcsTransientError` — исключения нет, check run выставлен по базе, warning с id прогона; ошибка кода (`AttributeError`) не гасится (`tests/application/test_publish_review.py`, фейк `RecordingVcsGateway.threads_error`)
- [x] 1.2 Красный сквозной тест: GraphQL отвечает 502 — прогон `completed`, одно ревью, один check run, DLQ пуст (`tests/e2e/test_rules_e2e.py`, `FakeGitHub.graphql_status`)
- [x] 1.3 `publish_review`: вызов `sync_open_threads_from_host` в `try/except VcsError` с warning, gate после него без изменений
- [x] 1.4 `docs/testing/RULES_E2E_REPORT.md` §5.3 — исправлено

## 2. Ответы в треды — best-effort

- [x] 2.1 Красные тесты `publish_review`: ответ в первый тред упал — второй опубликован, gate выставлен, warning с id прогона и корнем; закрытие упало после ответа — ответ в базе, `thread_resolved` не выставлен, gate выставлен; `VcsError` на инкрементальном диффе — ответ есть, тред не закрыт; программная ошибка пробрасывается (`tests/application/test_publish_review.py`, фейк `RecordingVcsGateway.diff_error`, `reply_errors`, `resolve_errors`)
- [x] 2.2 `_publish_thread_follow_ups`: `VcsError` на диффе, ответе и закрытии — warning и следующий тред; запись ответа до закрытия, `thread_resolved` — отдельной транзакцией
- [x] 2.3 Второй follow-up на уже отвеченный в этом прогоне тред пропускается: его запись упала бы на `uq_published_comments_finding` после ответа на GitHub (тест `test_two_follow_ups_for_one_thread_publish_one_reply`)
