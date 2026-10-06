## 1. Домен: чистые функции (без инфраструктуры и зависимостей)

- [x] 1.1 `parse_diff(raw: str) -> list[ParsedFile]` в
  `app/domain/diff_parser.py`: разбор unified diff на файлы и hunk'и
  (переиспользуется существующая сущность `Hunk`), точные номера строк из
  `@@ -a,b +c,d @@`, контекстные строки сдвигают счётчики, но не попадают в
  changed-множества, маркер `\ No newline at end of file` не ломает разбор,
  пустой дифф — `[]`; новая сущность `ParsedFile` (frozen dataclass:
  `file_path`, `old_path`, `is_binary`, `hunks`) в `app/domain/entities.py`;
  тесты на литеральных строках без базы и сети —
  `tests/domain/test_diff_parser.py` (spec: webhook-intake — Разбор диффа
  на файлы и hunk'и)
- [x] 1.2 `is_filterable(file_path, is_binary) -> bool` и
  `filter_diff_files(files) -> list[ParsedFile]` в
  `app/domain/diff_parser.py`: lock-файлы, `*.min.js`/`*.min.css`,
  `*.generated.*`/`*.pb.go`, бинарные, `vendor/`/`node_modules/`; паттерны —
  один источник в домене; обычные исходники проходят (spec: webhook-intake —
  Фильтрация шума в диффе)
- [x] 1.3 `verify_hmac(body: bytes, signature: str, secret: bytes) -> bool`
  в `app/domain/hmac.py`: SHA-256, формат `sha256=<hex>`,
  constant-time сравнение (`hmac.compare_digest`), только stdlib (spec:
  webhook-intake — Проверка подлинности вебхука)
- [x] 1.4 `extract_github_event(payload: dict) -> WebhookEvent | None` в
  `app/domain/webhook.py`: `opened`/`synchronize` → событие, `reopened` и
  прочие → `None` (ignored); извлекает `installation_id` (отсутствие
  `installation` — `None`, не crash), `repo_full_name`, `repo_provider_id`,
  `pr_number`, `head_sha`, `source_branch`, `target_branch`, `title`,
  `author`; новая сущность `WebhookEvent`; тесты на записанных payload'ах —
  `tests/fixtures/github_webhook_opened.json`,
  `tests/fixtures/github_webhook_synchronize.json` (spec: webhook-intake —
  Запуск ревью событиями pull request)
- [x] 1.5 Чекпойнт: `uv run pytest tests/domain/` green; `uv run ruff
  check .`, `uv run mypy .`, `uv run lint-imports` — clean; новых
  runtime-зависимостей нет

## 2. Порт VcsGateway + адаптер GitHub App

- [x] 2.1 Порт `VcsGateway` в `app/application/ports/vcs_gateway.py`
  (`typing.Protocol`): `fetch_diff(repo_full_name, pr_number,
  installation_id)` и `fetch_pr_metadata(...)` — доменные типы и примитивы,
  без `httpx`/`Session`; `installation_id: int` — параметр каждого метода;
  метода `request_reviewer` нет; сущность `PRMetadata` (frozen dataclass) в
  `app/domain/entities.py`; экспорт порта в
  `app/application/ports/__init__.py`; `tests/test_container.py` — у каждого
  порта адаптер (spec: webhook-intake — Получение диффа через VCS-шлюз)
- [x] 2.2 Модуль `app/infrastructure/vcs/github_auth.py`: JWT RS256 из
  `github_app_id` + PEM, обмен на installation token per `installation_id`,
  кэш dict с expiry внутри адаптера, автообновление просроченного; добавить
  `httpx` (из dev в runtime), `pyjwt`, `cryptography` через `uv sync
  --all-extras`; `uv.lock` обновлён; тесты на тестовом PEM и
  `httpx.MockTransport` — `tests/infrastructure/test_github_auth.py`
  (design D2, D7, D8)
- [x] 2.3 Адаптер `GitHubVcsGateway` в `app/infrastructure/vcs/github.py`:
  дифф — `GET /repos/{owner}/{repo}/pulls/{n}` с
  `Accept: application/vnd.github.v3.diff`, метаданные — тот же endpoint с
  JSON-заголовком; перевод GitHub JSON → `PRMetadata` без бизнес-правил;
  exponential backoff на 429/5xx, исчерпание — исключение адаптера; тесты на
  `httpx.MockTransport` — `tests/infrastructure/test_github_vcs_gateway.py`
- [x] 2.4 Wiring: фабрика `vcs_gateway()` в `Container`
  (`app/infrastructure/container.py`), привязка `GitHubVcsGateway`; вызывающий
  код адаптер не создаёт; `uv run pytest tests/test_container.py` green

## 3. Порт JobQueue + адаптер RabbitMQ

- [x] 3.1 Порт `JobQueue` в `app/application/ports/job_queue.py`
  (`typing.Protocol`, единственный метод `enqueue(job: ReviewJob) -> None`);
  сущность `ReviewJob` (frozen dataclass: `job_id: UUID` — UUIDv7 из
  `app/domain/ids.py`, `review_run_id`, `repository_full_name`,
  `repository_provider_id`, `pr_number`, `head_sha`, `base_sha | None`,
  `action`, `priority`); экспорт порта; обновление
  `tests/test_container.py` (spec: webhook-intake — Постановка задачи ревью
  в очередь)
- [x] 3.2 Адаптер `PikaJobQueue` в `app/infrastructure/queue/rabbitmq.py`:
  сериализация `ReviewJob` в JSON по `docs/SYSTEM_DESIGN.md` §4.2 (тело —
  только доменные данные), `priority` — свойство AMQP, очередь одна с
  `x-max-priority`; `rabbitmq_url` только через `Settings`, соеди́нение с
  брокером — ленивое, при первом `enqueue`: пустой или недоступный брокер
  громко падает при первом использовании, а не на старте приложения
  (design D8); зависимость `pika` через `uv sync --all-extras`;
  тест с моком pika-канала — `tests/infrastructure/test_rabbitmq_queue.py`
- [x] 3.3 Wiring: фабрика `job_queue()` в `Container`, привязка
  `PikaJobQueue`; `uv run pytest tests/test_container.py` green
- [x] 3.4 Чекпойнт: `uv run pytest` green (новые тесты адаптеров на моках);
  `uv run ruff check .`, `uv run mypy .`, `uv run lint-imports` — clean
  (`httpx`/`pika` только в infrastructure); `uv sync --all-extras`
  идемпотентен, `uv.lock` закоммичен

## 4. Use case + эндпоинт (vertical slice)

- [x] 4.1 Use case `HandleWebhookEvent` в
  `app/application/use_cases/handle_webhook.py`: extract → найти/создать
  `Repository` + `MergeRequest` через `UnitOfWork` → дифф и метаданные
  через `VcsGateway` по `installation_id` из события (дифф use case НЕ
  разбирает: сбой VCS — 502 до записей, разбор выполняет воркер той же
  чистой функцией, design D3) → `fetch_pr_metadata` для `base_sha` →
  `ReviewRun` (status `queued`, trigger `webhook`) → `enqueue(ReviewJob)`;
  зависимости — только порты
  (`UnitOfWork`, `VcsGateway`, `JobQueue`); `reopened`/прочие — результат
  «ignored» без записей; незарегистрированный репозиторий — ignored (202),
  без регистрации на лету; второй активный прогон на тот же коммит не
  создаётся и не падает (частичный уникальный индекс); юнит-тесты на фейках
  портов — `tests/application/test_handle_webhook.py` (spec: webhook-intake —
  все требования; design D3–D6, D9)
- [x] 4.2 Эндпоинт `POST /webhooks/github` в `app/api/webhooks.py`,
  регистрация в `app/api/factory.py`: сырое тело до парсинга JSON,
  `verify_hmac` против `Settings.github_webhook_secret`; ответы: 202 (прогон
  создан), 202 (ignored), 401 (подпись неверна/отсутствует; пустой секрет
  отклоняет всё), 502 (ошибка VCS); настройки `github_app_id`,
  `github_app_private_key`, `github_webhook_secret` в `Settings` — вместе с
  кодом, который их читает, опциональные с дефолтом `""` (design D8);
  роутер зависит от портов, адаптеры не называет;
  тесты на `TestClient` с моками — `tests/api/test_webhooks.py` (spec:
  webhook-intake — Проверка подлинности вебхука; Запуск ревью событиями
  pull request; Незарегистрированный репозиторий игнорируется)
- [x] 4.3 Чекпойнт: `POST /webhooks/github` с payload `pull_request.opened`
  и валидной подписью → 202, `MergeRequest` + `ReviewRun` (queued,
  trigger=webhook), задача сериализована по §4.2; неверная подпись → 401,
  записей нет; `reopened` → 202, записей нет; `uv run pytest` green

## 5. E2E, документация, синхронизация спеки

- [x] 5.1 Интеграционный тест `tests/api/test_webhooks_e2e.py` (маркер
  `integration`, `TEST_DATABASE_URL`, осознанный skip без базы) на записанных
  payload'ах и реалистичном unified diff: подпись, разбор, фильтрация
  lock/`.min.js`/бинарных, записи в БД (`head_sha` = head из payload,
  trigger = `webhook`), формат сообщения очереди по §4.2 (spec:
  webhook-intake — все требования)
- [x] 5.2 Документация: в `docs/BACKEND_ARCHITECTURE.md` перевести
  `VcsGateway` и `JobQueue` в «Реализованы» с адаптерами, threat model
  ссылается на реализованную проверку подписи; в `docs/SYSTEM_DESIGN.md`
  — сплошные узлы API-приёма и очереди; `uv run pytest
  tests/test_docs.py` green
- [x] 5.3 Синхронизация delta spec в `openspec/specs/webhook-intake/` и
  архивация change в `openspec/changes/archive/`; `uv run alembic heads` —
  одна голова `0002`; финал: `uv run ruff check .`, `uv run lint-imports`,
  `uv run pytest` — green
