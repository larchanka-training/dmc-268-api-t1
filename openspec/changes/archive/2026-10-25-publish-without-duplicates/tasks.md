## 1. Без повтора создания ревью и ответа

- [x] 1.1 Красные тесты: сквозной — `POST /reviews` записал ревью и ответил 502, второго ревью нет, сводка и inline в базе (`tests/e2e/test_publish_no_duplicates_e2e.py`, `FakeGitHub.create_review_failures`); маркер в ревью человека и другого бота своим не считается; отказ лимита до записи — пауза по `Retry-After`, поиск, второй `POST`, одно ревью; адаптер — после 502 и таймаута поиск находит ревью без второго `POST`, все попытки без своего ревью — `POST` ровно `max_attempts` раз и сбой, ответ в тред на 502 не повторяется (`tests/infrastructure/test_github_review_publish.py`)
- [x] 1.2 Адаптер `GitHubReviewClient.publish_pull_request_review`: маркер `review_run_marker`, `POST` с `repeatable=False`, `_create_review`: пауза `GitHubVcsGateway.wait_before_retry`, поиск `_find_review_by_marker` по `bot_login` (`GET /app`), повтор `POST` только без своего ревью; ответ в тред — `repeatable=False`; исключение `VcsUnconfirmedError`
- [x] 1.3 Порт `VcsGateway.publish_pull_request_review(..., review_run_id)`, вызов из `publish_review`

## 2. Потерянный id inline не теряет critical

- [x] 2.1 Красные тесты: сквозной — `GET …/comments` отвечает 502, сводка записана, прогон `completed`, check run `failure`; прикладной — то же на `RecordingVcsGateway.inline_ids_lost`; пересчёт по вебхуку закрытия треда видит `critical` без id (`tests/application/test_sync_thread_resolution.py`)
- [x] 2.2 Чистая функция `untracked_findings` (`app/domain/merge_gate.py`) с тестом; `collect_open_findings(..., head_run_id)`; адаптер не падает на списке комментариев
- [x] 2.3 `refresh_merge_gate_for_merge_request`: прогон на head — `find_reviewed`

## 3. Повторы по лимитам GitHub

- [x] 3.1 Красные тесты адаптера: 429 с `Retry-After` — пауза 7 с; 403 с `x-ratelimit-remaining: 0` — до `x-ratelimit-reset` и секунда; без заголовков — минута; сброс дальше бюджета — сбой без паузы; 403 доступа не повторяется; GraphQL `RATE_LIMITED` при 200 — повтор; check run на 502 — повтор (`tests/infrastructure/test_github_vcs_gateway.py`, `test_github_review_publish.py`)
- [x] 3.2 Один цикл `GitHubVcsGateway._request` вместо `_get` и `_request_json`; `_rate_limited`, `_rate_limit_wait`; `clock` и `rate_limit_budget_seconds` в конструкторе

## 4. Таймауты, check run

- [x] 4.1 `VcsClientLimits`: `API_VCS_LIMITS` (5 с, бюджет 0) по умолчанию в `build_container`, `WORKER_VCS_LIMITS` (30 с, 60 с) в `run_worker`; тест `tests/test_container.py`; проходу повтора доставок — `Container.redelivery_vcs_gateway` с лимитами API
- [x] 4.2 Красный тест: сбой `set_merge_gate_check` после публикации — прогон не падает, warning с id прогона; `publish_review` — `try/except VcsError`

## 5. Документация

- [x] 5.1 `docs/BACKEND_ARCHITECTURE.md` (`VcsGateway`), `docs/SYSTEM_DESIGN.md` («Rate Limits & Fallbacks»)
