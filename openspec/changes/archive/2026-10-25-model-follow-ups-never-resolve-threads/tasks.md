## 1. Воспроизведение

- [x] 1.1 Красные тесты на коде до правки (`tests/application/test_prompt_injection.py`): инъекция в диффе, модель возвращает `superseded` + `resolveThread: true` на открытый тред `critical` без изменения строки якоря — тред закрыт, gate `success`; та же инъекция в коммите, который меняет строку якоря, со статусом `fixed` — тред закрыт, gate `success`. После правки: тред открыт, ответа бота в треде нет, прогон `completed`, gate `failure`
- [x] 1.2 Фейк `review_response(thread_follow_ups=…)` в `tests/fakes/llm_gateway.py`: follow-up идёт через ту же проверку формы, что ответ провайдера

## 2. Правка

- [x] 2.1 `to_review_result` (`app/application/llm_schema.py`) отбрасывает `threadFollowUps`; `LlmThreadFollowUp` и `LlmReviewResult.thread_follow_ups` удалены из порта (тест `tests/application/test_llm_schema.py`)
- [x] 2.2 `publish_review` без `_publish_thread_follow_ups`, `_may_resolve_thread`, `_anchor_line_changed`, `_hunks_between_commits` и параметра `diff_max_bytes` (`app/application/publish_review.py`, `app/application/review_pipeline.py`)
- [x] 2.3 Порт `VcsGateway` без `reply_to_review_comment` и `resolve_review_thread_for_comment`; адаптеры `GitHubVcsGateway`, `GitHubReviewClient` и фейки — тоже
- [x] 2.4 Домен без `format_thread_reply` и `ThreadCoverage.superseded`
- [x] 2.5 Тесты ответов и закрытия тредов из `tests/application/test_publish_review.py` и `tests/infrastructure/test_github_review_publish.py` удалены вместе с кодом

## 3. Документация

- [x] 3.1 `docs/openapi/llm_review_output.schema.json`: описание `threadFollowUps` — поле игнорируется
- [x] 3.2 `docs/erd.md`, `docs/BACKEND_ARCHITECTURE.md`
