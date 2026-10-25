## Why

Хвосты #76, не вошедшие в PR #184 (задача #185). Адаптер GitHub повторяет `POST …/reviews` и ответ в тред на 5xx, а идемпотентности у этих запросов нет: 502 после записи на стороне GitHub даёт второе ревью или прогон `failed` при уже опубликованном ревью. Если после создания ревью падает `GET …/reviews/{id}/comments`, прогон уходит в `failed` без единой записи в `published_comments`: `critical` на GitHub есть, а merge gate его не видит, и вебхук закрытия соседнего треда выставит `success`. Лимиты GitHub пережидаются паузой 0,5–1 с вместо `Retry-After`, GraphQL-лимит при HTTP 200 не повторяется, у API и воркера один клиент с таймаутом httpx по умолчанию (5 с). Сбой самого check run после публикации переводит прогон в `failed`.

## What Changes

- Тело ревью несёт скрытый маркер `<!-- dmc-268-review-run:<id прогона> -->`. Ответа на `POST …/reviews` нет (обрыв, таймаут, 5xx, отказ лимита) — пауза, затем поиск ревью с маркером среди ревью бота App (`<slug>[bot]`, slug — `GET /app`): нашлось — опубликовано, нет — следующий `POST`, до предела попыток. Ответ в тред не повторяется.
- id сводки возвращается, как только ревью создано; не полученный список inline-комментариев не роняет публикацию. Находка опубликованного ревью без записи inline считается для merge gate открытой — на публикации и при пересчёте по вебхуку закрытия треда.
- Один цикл повторов в адаптере вместо `_get` и `_request_json`: 5xx — backoff; 429, 403 лимита и GraphQL `RATE_LIMITED` при 200 — пауза по `retry-after` / `x-ratelimit-reset` / минута, в пределах бюджета ожидания.
- Клиент VCS-шлюза: API — таймаут 5 с и без ожидания лимита, воркер — 30 с (подключение 5 с) и 60 с ожидания лимита на запрос.
- Сбой `set_merge_gate_check` после публикации — warning, прогон `completed`.

## Capabilities

### Modified Capabilities

- `review-pipeline`: ADDED «Ревью и ответ в тред не публикуются дважды», «Повтор запроса к GitHub по его лимитам», «Потерянный id inline не снимает блокировку»; MODIFIED «Merge gate по критичным открытым тредам» — check run тоже best-effort.

## Impact

- Код: `app/infrastructure/vcs/github.py`, `github_review.py`, `errors.py`, `app/infrastructure/container.py`, `app/worker/factory.py`, `app/application/publish_review.py`, `merge_gate_publish.py`, `sync_thread_resolution.py`, `app/domain/merge_gate.py`, порт `VcsGateway.publish_pull_request_review` (`review_run_id`).
- Тесты: сквозные `tests/e2e/test_publish_no_duplicates_e2e.py`, адаптер, прикладной слой, домен; фейки `FakeGitHub` (`GET /app`, список ревью, отказы создания ревью и списка комментариев) и `RecordingVcsGateway`.
- Документация: `docs/BACKEND_ARCHITECTURE.md`, `docs/SYSTEM_DESIGN.md`.
- Схема базы, контракт API и настройки не меняются. Разрешений App не прибавляется: `GET /pulls/{n}/reviews` читается тем же Pull requests: read, `GET /app` — JWT приложения.
