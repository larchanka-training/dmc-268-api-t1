## Why

`backend-architecture` и `review-data-model` построили слои, модель
персистентности и composition root, но у конвейера ревью до сих пор нет
входной точки: вебхуки GitHub не принимаются, дифф не достаётся из VCS,
задача ревью не появляется в очереди. Это первый шаг Definition of Done —
«при поступлении реального вебхука бэкенд валидирует подпись, скачивает
diff, разбирает его на файлы и строки и передаёт структуру в задачу
очереди» (Context Level 1). Без приёмной части остальной конвейер — воркер
и сборка RAG-контекста (ветка `feat/system_design_rag`) — не имеет входных
данных.

## What Changes

- Эндпоинт `POST /webhooks/github`: чтение сырого тела до парсинга JSON,
  проверка подписи HMAC-SHA256 по заголовку `X-Hub-Signature-256`
  (формат `sha256=<hex>`, constant-time сравнение); без подписи или с
  неверной — HTTP 401 без каких-либо записей.
- Порт `VcsGateway` (`fetch_diff`, `fetch_pr_metadata`, оба принимают
  `installation_id` параметром) и адаптер GitHub App: JWT RS256 из App ID +
  PEM private key, обмен на installation token, кэш токенов с expiry внутри
  адаптера.
- Чистый парсер unified diff `parse_diff` (файлы и hunk'и с точными
  номерами строк из заголовков `@@ -a,b +c,d @@`) и фильтрация шума
  `filter_diff_files`: lock-файлы, минифицированные, сгенерированные,
  бинарные и вендорные пути.
- Порт `JobQueue` (единственный метод `enqueue`) и адаптер `PikaJobQueue`
  (RabbitMQ): тело сообщения — только доменные данные по формату
  `docs/SYSTEM_DESIGN.md` §4.2, `priority` — свойство AMQP, очередь одна, с
  `x-max-priority`.
- Use case `HandleWebhookEvent`: извлечь событие → найти/создать
  `Repository` + `MergeRequest` → достать дифф через `VcsGateway`
  (без разбора: сбой VCS даёт 502 до создания записей) → создать
  `ReviewRun` (status `queued`, trigger `webhook`) → поставить задачу
  в очередь. Разбор и фильтрацию диффа выполняет воркер конвейера ревью
  (design D3). Зависимости — только порты.
- Новые сущности домена: `WebhookEvent`, `ParsedFile`, `PRMetadata`,
  `ReviewJob`.
- Новые runtime-зависимости: `httpx` (переводится из dev), `pyjwt`,
  `cryptography`, `pika` — только через `uv`, с обновлением `uv.lock`.
- Новые настройки: `github_app_id`, `github_app_private_key`,
  `github_webhook_secret`, `rabbitmq_url`. Настройка `github_installation_id`
  не вводится; `github_bot_username` тоже — потребителя в этом change'е нет,
  появится на тикете воркера (см. design D8).
- Миграций схемы нет: таблицы `repositories`, `merge_requests`,
  `review_runs` уже хранят всё, что записывает вебхук; `uv run alembic
  heads` остаётся на ревизии `0002`.

Non-goals — этого в change deliberately нет:

- Воркер конвейера и сборка RAG-контекста (Context Levels 2+) — ветка
  `feat/system_design_rag`: вычитка очереди, сборка контекста, вызов LLM.
- Публикация комментариев и статусов на VCS — `VcsGateway` здесь только
  читает (дифф и метаданные PR), публикация приходит со своим change.
- Регистрация репозиториев: отдельный эндпоинт с авторизацией. Вебхук
  обрабатывает только уже зарегистрированные репозитории, регистрации на
  лету нет — авто-регистрация противоречит threat model
  («подписать чужой репозиторий»).
- GitLab-адаптер `VcsGateway`: provider-нейтральная модель (`Provider`
  enum, `provider_id`) уже существует; добавление GitLab — новый адаптер и
  экстрактор payload'а, без миграции схемы.
- `IdempotencyStore`: дедупликация повторных доставок вебхуков — отдельное
  изменение; от второго активного прогона на тот же коммит защищает уже
  существующий частичный уникальный индекс.
- Персистенция диффа: дифф не сохраняется ни вебхуком, ни воркером;
  `context_payloads` — территория воркера.

## Capabilities

### New Capabilities

- `webhook-intake`: приёмная часть конвейера ревью — проверка подлинности
  вебхука GitHub, обработка событий pull request, игнор безвредных
  событий, получение диффа и метаданных PR через VCS-шлюз, постановка
  задачи ревью в очередь; разбор диффа на файлы и hunk'и с фильтрацией
  шума выполняет воркер конвейера ревью той же чистой функцией домена
  (design D3).

### Modified Capabilities

None. Хранимая модель (`review-data-model`) уже покрывает записи, которые
создаёт вебхук (`Repository`, `MergeRequest`, `ReviewRun`, частичный
уникальный индекс на активный прогон); этот change меняет поведение приёма,
а не требования к данным.

## Impact

**Code** — `app/domain` (`diff_parser.py`, `hmac.py`, `webhook.py` — новые
чистые функции; `entities.py` — новые сущности), `app/application`
(`ports/vcs_gateway.py`, `ports/job_queue.py`,
`use_cases/handle_webhook.py`), `app/infrastructure` (`vcs/github_auth.py`,
`vcs/github.py`, `queue/rabbitmq.py`, `container.py`), `app/api`
(`webhooks.py`, `factory.py`), `app/config.py`. Существующие тесты
`tests/test_container.py` расширяются: у каждого объявленного порта —
адаптер.

**Dependencies** — `httpx` переезжает из dev extra в runtime; добавляются
`pyjwt`, `cryptography`, `pika` (runtime). Всё через `uv sync
--all-extras` с обновлением `uv.lock`; `httpx` и `pika` остаются
запрещёнными для `domain`/`application` в `lint-imports`.

**Схема БД** — без изменений; Alembic остаётся на одной голове `0002`.

**Инфраструктура** — RabbitMQ уже описан в `docs/SYSTEM_DESIGN.md` как
брокер очереди задач; новых внешних систем не появляется.

**Документация** — после реализации: `docs/BACKEND_ARCHITECTURE.md`
(порты `VcsGateway` и `JobQueue` переходят в «Реализованы»),
`docs/SYSTEM_DESIGN.md` (диаграмма), синхронизация спеки в
`openspec/specs/webhook-intake/` и архивация change.
