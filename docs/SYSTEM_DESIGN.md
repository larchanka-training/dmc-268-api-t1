# Git ИИ Ревьюер кода — системный дизайн

Система автоматического ревью кода: отслеживает изменения в pull request'ах, собирает контекст вокруг диффа, передаёт его на ревью LLM и публикует результат обратно в VCS.

> **Разделение ответственности документов.** Этот документ владеет топологией развертывания, layout'ом обменов и очередей RabbitMQ, фронтендом, платежным UX и выбором внешних провайдеров (LLM provider / LLM API). Источник истины по слоям, портам, схеме данных, миграциям и threat model — [BACKEND_ARCHITECTURE.md](BACKEND_ARCHITECTURE.md); по модели данных — [erd.md](erd.md). При расхождении приоритет у BACKEND_ARCHITECTURE.md.

## 1. Диаграмма компонентов

```mermaid
flowchart TB
    %% External
    GH[GitHub<br/>PR / Push Events]
    GHR[GitHub<br/>Review Comments / Status]
    PP[Payment Provider<br/>Stripe / etc.]
    OLLAMA[Ollama · local model<br/>in our network · primary]
    EURO[Eurorouter · hosted LLM API<br/>opt-in fallback only]
    FE[Frontend<br/>PR History · Settings · Usage/Billing · Admin]

    %% Infrastructure
    MQ[RabbitMQ<br/>Message Broker / Queues]
    DB[(PostgreSQL<br/>only PostgreSQL: native enums · JSONB<br/>partial unique index)]

    %% Modular monolith
    subgraph MONOLITH["Modular monolith: one codebase, one image, two processes"]
        API["app/api · uvicorn app.main:app<br/>webhook intake · HMAC verify · validation<br/>rate limiting · idempotency · REST for the frontend"]
        WORKER["app/worker · python -m app.worker<br/>review consumer · stale-run sweep"]
        ORCH["Application use cases<br/>context assembly · prompt assembly (pure functions)<br/>review call · screening · verifier call<br/>next_status"]
        PARSE["DefinitionExtractor port · tree-sitter adapter<br/>outline of changed files for the ast tier<br/>used by orchestrator code, never by the model"]
        CACHE["CacheStore port · in-process TTL dict<br/>repo rules by (repo, base_sha)"]
        VCS["VcsGateway port · GitHub REST adapter<br/>diffs · metadata · file contents · comments · statuses<br/>GitHub App auth · backoff inside the adapter"]
        LGW["LlmGateway port · fallback adapter<br/>Ollama first · Eurorouter only by flag<br/>answer checked against the response model"]
        REPOS["Repository ports + UnitOfWork<br/>SQLAlchemy adapters"]
    end

    %% Planned seams
    subgraph PLANNED["Planned seams (designed, not built)"]
        PAY["Payments / accounts<br/>plans · subscriptions · billing"]
    end

    %% Event flow
    GH -->|Webhook, HMAC-signed| API
    FE <-->|REST| API
    API -->|JobQueue enqueue| MQ
    MQ --> WORKER
    WORKER --> ORCH
    API -->|settings · history · usage| ORCH

    %% VCS
    ORCH <-->|PR data / diffs / files| VCS
    ORCH --> PARSE
    ORCH <--> CACHE
    VCS -->|review comments / status| GHR

    %% LLM
    ORCH <--> LGW
    LGW <-->|primary| OLLAMA
    LGW -.->|opt-in fallback| EURO

    %% Data and telemetry
    ORCH -->|runs · findings · context · comments · telemetry| REPOS
    REPOS --> DB

    %% Planned seams
    API -.-> PAY
    PAY -.->|subscription / payment| PP

    %% Styling
    classDef external fill:#f5f5f5,stroke:#555,color:#111;
    classDef core fill:#e8f4ff,stroke:#2878c8,color:#111;
    classDef planned fill:#eef8ef,stroke:#368a4a,color:#111;
    classDef infra fill:#fff6e8,stroke:#c88719,color:#111;
    classDef db fill:#f7eefc,stroke:#8a4db8,color:#111;

    class GH,GHR,PP,OLLAMA,EURO,FE external;
    class API,WORKER,ORCH,VCS,LGW,REPOS,PARSE,CACHE core;
    class PAY planned;
    class MQ infra;
    class DB db;
```

Сплошные узлы и связи — построено. Пунктир — запланированные швы (таблица «Designed, not built» в BACKEND_ARCHITECTURE.md): порт не пишется раньше первого вызова, шов описывается заранее и бесплатно.

## 2. Общая архитектура (компоненты и границы ответственности)

Система — **событийно-ориентированный модульный монолит**: один кодбейс, один образ, два процесса, которые общаются через очередь, а не по HTTP. Это осознанно не микросервисы: микросервисы решают организационную проблему независимых команд, которой здесь нет, а их цену — сетевые вызовы вместо вызовов функций, частичные отказы, версионирование контрактов, идемпотентность на каждой границе — платить нечем. Полное обоснование — раздел «Process shape» в BACKEND_ARCHITECTURE.md.

Границы компонентов ниже — логические (слои `app/api`, `app/application`, `app/domain`, `app/infrastructure`; направление импортов проверяет import-linter в CI), а не деплойментные.

### 2.1 Границы ответственности

- **Frontend (FE)**: настройки репозиториев, история PR, администрирование, usage/billing. Общается с системой только через REST API монолита (`app/api`).
- **`app/api` — HTTP-точка входа (`uvicorn app.main:app`)**: построено — приём вебхуков GitHub с проверкой HMAC-подписи (constant-time, `app/domain/hmac.py`) и постановка задач в очередь через порт `JobQueue`, общий поток событий кабинета по SSE (`GET /api/v1/events`: прогоны пишет воркер, при коммите записи PostgreSQL шлёт `NOTIFY`, API слушает `LISTEN` одним соединением на процесс и дочитывает изменённые прогоны — отдельного брокера событий нет); запланированные швы — rate limiting (fixed-window, порт `RateLimiter`), идемпотентность повторных доставок (порт `IdempotencyStore`), приём вебхуков GitLab, REST для фронтенда.
- **RabbitMQ**: брокер сообщений; адаптер порта `JobQueue` с единственным методом `enqueue` построен (`RabbitMQJobQueue`: тело — только доменные данные, переиспользуемое соединение с publisher confirms, DLX/DLQ для отклонённых сообщений). Layout обменов — зона ответственности этого документа (см. 4.2).
- **`app/worker` — консьюмер ревью (`python -m app.worker`)**: ходит минутами, ограничен латентностью модели; также выметает зависшие прогоны (`find_stale`, `last_progress_at`).
- **Оркестрация - use cases слоя `application`**: сценарий ревью - сборка контекста и запроса (чистые функции), вызов ревью через `LlmGateway`, детерминированный отсев (`validate_anchor`, ссылка на правило, `deduplicate`), вызов верификатора, который может только отбросить замечание, публикация через `VcsGateway`; жизненный цикл прогона - `next_status`. Ретраи с exponential backoff живут в адаптерах. **Модель не получает инструментов и никогда не выбирает действий** - структурная защита от prompt injection (threat model в BACKEND_ARCHITECTURE.md).
- **`VcsGateway` (порт; первый адаптер — GitHub REST)**: диффы (unified diff; сегодня hunk несёт 3 строки контекста, которые отдаёт GitHub, ±30 строк — запланированный уровень `surrounding`), метаданные, комментарии, статусы; аутентификация GitHub App с краткосрочными installation-токенами; содержимое файла на коммите — метод порта `fetch_file` (файлы диффа для уровня `ast` — по `head_sha`, `.review/rules.md` — по `base_sha`; лимиты применяет вызывающий). Payload'ы, синтаксис комментариев, auth и backoff — детали адаптера.
- **`LlmGateway` (порт)**: транспорт к модели. Провайдеры - локальная модель в Ollama (основная) и Eurorouter по OpenAI-совместимому протоколу (фоллбэком при `LLM_HOSTED_FALLBACK=true` или основным и единственным при `LLM_BACKEND=eurorouter` — так работает стенд, #245); порядок не выбирается, а собирается composition root из конфигурации, заглушка - только явным `LLM_BACKEND=stub`. Ответ проверяется Pydantic-моделью из запроса с повторами, ключи Eurorouter ротируются. Сборка промпта и проверка окна модели - чистые функции; адаптер только транспортирует. Включение hosted-фоллбэка - решение по безопасности (крупнейший путь эксфильтрации), не только операционное; детали - в BACKEND_ARCHITECTURE.md.
- **Данные и телеметрия**: не отдельный сервис. Стоимость прогона — колонки `model`, `tokens_used`, `duration_seconds`, `failure_reason` в `review_runs`; per-call аудит (вызовы LLM и инструментов) — будущий шов, его первый заказчик — биллинг по токенам.
- **Payments / accounts**: сегодня спроектирована только таблица `accounts` — она придёт вместе со своим портом (регистрация репозитория с проверкой прав на него); тарифы, подписки, Stripe — запланированный шов. Приоритет платных тарифов — требование к обслуживанию очереди (раздел 4.2), код порта оно не затрагивает.
- **Контекст без клона (принцип)**: репозиторий никогда не клонируется целиком и не хранится — Repository Indexer, Vector DB и Repository Storage исключены из дизайна. Весь контекст строится из данных PR через `VcsGateway`: дифф, AST-окружение изменённых строк (уровень `ast`) и правила владельца из целевой ветки; запланированы ±30 строк вокруг изменений, полные тексты изменённых файлов и сигнатуры из файлов, на которые ссылаются импорты. Анализ кода (tree-sitter за портом `DefinitionExtractor`) идёт над файлами, полученными через `VcsGateway`, и используется кодом оркестратора, а не моделью.

## 3. Потоки данных

1. **Инициация**: вебхук GitHub (`pull_request` с действием `opened` или `synchronize`) приходит в `app/api`, проверяет HMAC-подпись, достаёт метаданные PR (без диффа) через `VcsGateway` (сбой VCS — 502 до записей), создаёт `MergeRequest` + `ReviewRun` (state `queued`) и ставит задачу в очередь — без диффа: его с лимитом размера достаёт воркер по `base_sha` и `head_sha` из задачи, он же разбирает его и фильтрует шум (capability `webhook-intake` в спеках). Источник прогона фиксируется колонкой `review_runs.trigger_source` (enum `TriggerSource`: `webhook` / `manual` / `mention`); сегодня работает `webhook`, а запуск по назначению бота ревьюером или упоминанию в комментарии (`manual` / `mention`) — запланированный шов. Событие `reopened` и прочие действия игнорируются (202): бот никогда не переназначается автоматически. Ревьюятся только свои PR: из ветки самого репозитория (запушить её может только тот, у кого есть право записи) или из форка от автора `OWNER` / `MEMBER` / `COLLABORATOR` (`author_association`). Остальные PR из форка игнорируются с записью в лог — временная защита ключа модели до биллинга. Репозиторий, не подключённый в сервисе, игнорируется; у выключенного (`DELETE /api/v1/repositories/{id}`) прогона нет, а на коммит ставится проверка `success` «Ревью выключено в сервисе».
2. **Подлинность и идемпотентность**: проверяется HMAC-подпись доставки (построено, constant-time по сырому телу до разбора payload'а); повторная доставка того же коммита не создаёт второй активный прогон — частичный уникальный индекс схемы (построено), а после завершённого не-`failed` прогона по вебхуку отвечает им, не создавая нового (построено, #75); упавшие доставки воркер переотправляет через `GET /app/hook/deliveries` и `POST …/attempts` (построено, #149). Порт `IdempotencyStore` — запланированный шов: займёт ключ через `INSERT ... ON CONFLICT DO NOTHING` и переиграет исход первой доставки, когда появится.
3. **Очередь**: задача публикуется в RabbitMQ через `JobQueue.enqueue`.
4. **Консюмер**: `app/worker` вычитывает сообщение (job); устойчивая сущность — `ReviewRun` (переименована из `ReviewJob`, чтобы строка БД не делила имя с сообщением очереди).
5. **Сбор контекста**: use case иерархически собирает контекст (раздел 5) и сохраняет показанное модели в `context_payloads`; редакция секретов до вставки — запланирована (задача #28).
6. **Генерация**: запрос, не помещающийся вместе с резервом ответа в окно модели, не отправляется; вызов ревью через `LlmGateway` - локальная модель, при её сбое и включённом фоллбэке Eurorouter; модель возвращает структурированные findings по Pydantic-модели ответа, а не прозу, ответ не по схеме повторяется.
7. **Валидация**: детерминированный отсев - `validate_anchor` отбрасывает замечания вне диффа, ссылка на правило проверяется по показанным модели правилам, `deduplicate` убирает повторы (отклонения считаются на прогоне); затем второй вызов - верификатор (Reflection Pass) - отбрасывает галлюцинации, а при сбое прогон продолжается с замечаниями после отсева (fail-open); переходы статуса - через `next_status`.
8. **Публикация**: `VcsGateway` публикует Review Comments и статусы; каждый опубликованный комментарий фиксируется в `published_comments`.
9. **Телеметрия**: `model`, `tokens_used`, `duration_seconds`, `failure_reason` пишутся в `review_runs`.

## 4. Правила взаимодействия с VCS, форматы очередей и кэширование

### 4.1 Взаимодействие с VCS (GitHub API)

- **Аутентификация**: GitHub Apps — краткосрочные installation access токены; утечка ограничена одной инсталляцией, а не всеми репозиториями владельца токена.
- **Rate Limits & Fallbacks**: повторы — внутри адаптера `VcsGateway`, одним циклом: 5xx — exponential backoff; 429 и 403 лимита (у GraphQL — ещё `RATE_LIMITED` при 200) — пауза по `retry-after`, `x-ratelimit-reset` или минута, в пределах бюджета ожидания (воркер — 60 с на запрос, API — 0: отказ уходит 5xx, доставку переотправит воркер). Идемпотентности у создания ревью нет: оно повторяется, только если после паузы ревью бота со скрытым маркером прогона в теле не нашлось. Таймаут клиента GitHub — 5 с в API (вебхук) и 30 с в воркере.
- **Подлинность вебхуков**: GitHub подписывает доставки HMAC по телу; без проверки любой, кто узнал endpoint, может заставить систему ревьюить что угодно и тратить бюджет модели.
- **Вход пользователей**: OAuth того же GitHub App выполняет бэкенд (`/api/v1/auth/{provider}/login` и `/callback`, порт `OAuthProvider`). Токен пользователя хранится зашифрованным: по нему GitHub отдаёт репозитории установок, доступные пользователю; ревью по-прежнему ходит installation-токенами. Как приложение, установка и подключение репозитория связаны — [`GITHUB_APP.md`](GITHUB_APP.md). Сессия — случайное значение в httpOnly cookie на общем origin интерфейса и API, в базе только его хэш, выход действует сразу. Изменяющие запросы с сессией сверяют `Origin`. Детали — `openspec/changes/archive/2026-10-17-auth-session/design.md` и спека `user-auth`.

### 4.2 Формат очереди задач (RabbitMQ) 

JSON-сообщение; тело несёт только доменные данные — метаданные доставки (маршрут, приоритет) живут в свойствах AMQP и в тело не дублируются. Очередь одна, с `x-max-priority`. Требование: прогоны платных тарифов обслуживаются раньше бесплатных; в AMQP оно отображается свойством `priority` (0–9), учитываемым среди сообщений, ожидающих в очереди. Как требование выражается в коде — зона ответственности BACKEND_ARCHITECTURE.md: порт `JobQueue` сведён к одному методу `enqueue(job)`, адаптер RabbitMQ выводит приоритет из самого job'а (тариф аккаунта и так в данных, слой `application` про тарифы не знает), поэтому layout обменов и приоритеты кода не затрагивают. **Пока:** `accounts`/тарифы не реализованы, поэтому каждое сообщение публикуется с одним и тем же фиксированным приоритетом — очередь уже готова к приоритизации, вычисление появится вместе с тарифами, без смены типа очереди.

```json
{
  "job_id": "uuid-1234",
  "review_run_id": "uuid-5678",
  "event_type": "pull_request",
  "action": "opened",
  "installation_id": 512804923,
  "repository": {
    "full_name": "owner/repo",
    "id": "987654"
  },
  "pull_request": {
    "number": 42,
    "head_sha": "abc123def",
    "base_sha": "fed654cba"
  }
}
```

`job_id` — идентификатор сообщения (UUIDv7, генерируется в домене, не базой, новый на каждую постановку); устойчивая строка — `ReviewRun`, воркер находит её по `review_run_id`; дедупликация повторных доставок — `IdempotencyStore`. `installation_id` — инсталляция провайдера для авторизации воркера при повторном получении диффа (разбор диффа — у воркера, тело сообщения дифф не несёт).

**Dead-letter queue.** Очередь `review_jobs` объявлена с
`x-dead-letter-exchange: review_jobs.dlx` — fanout-exchange, на который
забинжена очередь `review_jobs.dlq`. Воркер, обработка которого завершилась
исключением, отвечает `nack` без `requeue`: сообщение не крутится в основной
очереди по кругу, а ровно один раз перекладывается в `review_jobs.dlq` для
ручного разбора. Ретраев с backoff перед dead-letter пока нет — осознанное
упрощение, зафиксированное в `design.md` OpenSpec change'а
`add-rabbitmq-job-queue`.

### 4.3 Стратегия кэширования

- **CacheStore**: best-effort key/value с TTL; сначала in-process словарь — корректен, пока процесс один, Redis-адаптер появляется вместе со второй репликой. Промах — не ошибка. Семантический кэш LLM-запросов не планируется: ключ точный.
- **Кэш правил (построено)**: результат загрузки `.review/rules.md` — в `InMemoryCacheStore` по ключу `repo-rules:v1:{репозиторий}:{base_sha}`; сбой VCS не кэшируется.
- **Кэш файлов (запланировано, по измерениям)**: диффы и содержимое файлов, полученные через `VcsGateway`, — в CacheStore по неизменяемым ключам (репозиторий, путь, sha); содержимое по sha не меняется, промах — не ошибка.

## 5. Концепция сборщика контекста (Context Assembly)

Сборка контекста и промпта — чистая функция (адаптер `LlmGateway` только транспортирует). Репозиторий не клонируется и не хранится: каждый уровень собирается из данных PR и содержимого файлов по `head_sha` (правила — по `base_sha`), полученных через `VcsGateway`. Уровни записываются в `context_payloads.tiers` (`diff`, `surrounding`, `whole_file`, `ast`); сегодня пишутся `diff` и, при наличии AST-блоков, `ast`. Для соблюдения лимитов токенов данные передаются иерархически:

- **Diff (уровень изменений)**
    - **Данные:** стандартный unified diff (+ добавленные, − удалённые строки).
    - **Цель:** понимание того, какие конкретно строки модифицированы в PR.
- **Surrounding (окружающий код) — запланировано**
    - **Данные:** ровно ±30 строк вокруг каждого изменённого блока — контекстные строки unified diff'а, который возвращает `VcsGateway` (довыборка содержимого файла по `head_sha` — внутри адаптера); перекрывающиеся окна склеиваются чистой функцией сборки.
    - **Цель:** оценка логики изменения внутри конкретной функции (не нарушен ли цикл, обработка ошибок).
- **Whole File (уровень файла) — запланировано**
    - **Данные:** полный текст модифицируемого файла, полученный через `VcsGateway` по `head_sha` той же довыборкой (добавляется, если позволяет лимит окна контекста).
    - **Цель:** проверка импортов, глобальных констант и соответствия код-стайлу файла.
- **AST / Imports (архитектурный уровень)**
    - **Данные (построено):** окружение изменённых строк — `VcsGateway.fetch_file` довыбирает изменённые файлы по `head_sha` (лимиты `ast_max_files`, `ast_max_file_bytes`), tree-sitter за портом `DefinitionExtractor` отдаёт контур файла (Python, TypeScript/TSX, JavaScript, Go), чистая `select_scopes` оставляет определения, охватывающие изменённые строки, — родительский класс и сигнатуру метода, — плюс импорты файла.
    - **Данные (запланировано):** сигнатуры из файлов, на которые ссылаются импорты, — индекс определений основной ветки (change `add-tree-sitter-index`).
    - **Цель:** выявление сайд-эффектов — например, изменение аргументов функции в файле A, когда она также используется в файле B.
- **Контекст PR**
    - **Данные:** заголовок и описание PR (`fetch_pr_metadata`) и сообщения его коммитов от нового к старому (`fetch_pr_commit_messages`), каждый текст - в своём блоке недоверенных данных, поле пакета `pull_request_context`. Сбой получения деградирует пакет (`omitted`), а не прогон.
    - **Цель:** намерение автора, ради которого модель правильно читает дифф; в системное сообщение текст автора PR не попадает.
- **Правила владельца репозитория**
    - **Данные:** `.review/rules.md`, прочитанный через `VcsGateway` на `base_sha` — коммите целевой ветки, а не PR; разбор Markdown в правила (имя, паттерны, исключения, проверки), в пакет — только правила, применимые к файлам диффа. Кэш — `CacheStore` по ключу «репозиторий, base_sha». Нет файла, сбой, превышение лимита — стандартные критерии, прогон не прерывается.
    - **Цель:** ревью по конвенциям конкретного проекта (спеки `repo-rules`, `ai-review-prompting`).

Пакет собирает чистая `assemble_context` (тело `context-payload/v1`, один чанк); всё I/O — сценарий `build_review_context`. Сбой получения диффа завершает прогон ошибкой, сбои контекста PR, правил и AST деградируют пакет.

Безопасность и лимиты (угрозы — threat model в BACKEND_ARCHITECTURE.md):

- каждый текст из репозитория лежит в блоке `<<<untrusted kind=… id=…>>>` с границей от sha256 содержимого и никогда не конкатенируется в инструкцию; невидимые символы заменяются записью `<U+XXXX>`; правила читаются только из целевой ветки — защита от prompt injection через ревьюимый код и `rules.md`;
- редакция секретов до вставки в `context_payloads` — таблица хранит чужой исходный код verbatim (задача #28);
- жёсткий лимит пакета `context_max_tokens` (32 000 по умолчанию; оценка — байты UTF-8 / 4 сериализованного тела, средняя, а не верхняя граница; лимит — пакета, а не окна модели: запрос целиком вместе с резервом ответа сверяется с окном `llm_context_window_tokens` до отправки): приоритет — контекст PR в пределах своей доли (`pr_context_max_tokens`), правила в пределах своей доли, затем дифф, затем AST; не поместившееся перечисляется в `omitted`; лимиты на число и размер довыбираемых файлов. Лимита на длительность прогона пока нет.

AST-уровень — первый кандидат на вынос из монолита: если tree-sitter не устроит, выделяется сервис парсинга — один порт получает сетевой адаптер (Strangler Fig), структура кода не меняется.

## 6. Основной процесс ревью

**GitHub PR → Webhook (HMAC) → `app/api` → `JobQueue` / RabbitMQ → `app/worker` → Context Assembly → вызов ревью → отсев → вызов верификатора → `VcsGateway` → GitHub Review**

Построено: webhook intake с проверкой HMAC, постановка задачи в очередь (`JobQueue` → RabbitMQ), парсинг/фильтрация диффа чистыми функциями домена - на стороне воркера, Context Assembly (дифф, контекст PR, AST-окружение, правила владельца под лимитом токенов), транспорт к модели (`LlmGateway`: локальная модель в Ollama, Eurorouter - явным фоллбэком), детерминированный отсев, верификатор и публикация через `VcsGateway`. Запланированные швы: `IdempotencyStore` (в живой цепочке его место - между `app/api` и очередью); развёртывание Ollama на стенде - отдельная задача.
